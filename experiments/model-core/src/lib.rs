use serde::{Deserialize, Serialize};

pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
pub const INPUTS: usize = 5;
pub const CELLS: usize = 8;
pub const WIDTH: usize = INPUTS + CELLS;
pub const OPS: usize = 8;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct Input {
    pub state: i64,
    pub event: i64,
    pub config: i64,
}

impl Input {
    pub fn lanes(self) -> [i64; INPUTS] {
        [self.state, self.event, self.config, 0, 1]
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
pub struct Effect {
    pub accepted: bool,
    pub state: i64,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
pub struct Observation {
    pub input: Input,
    pub effect: Effect,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Gate {
    pub operation: [u8; OPS],
    pub left: [u8; WIDTH],
    pub right: [u8; WIDTH],
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Model {
    pub format: u8,
    pub gates: [Gate; CELLS],
    pub accept: [u8; WIDTH],
    pub next_state: [u8; WIDTH],
}

pub fn one_hot<const N: usize>(index: usize) -> [u8; N] {
    let mut values = [0; N];
    values[index] = 1;
    values
}

fn selected_index(mask: &[u8], visible: usize) -> Result<usize> {
    if mask.iter().any(|&v| v > 1)
        || mask.iter().map(|&v| usize::from(v)).sum::<usize>() != 1
        || mask[visible..].contains(&1)
    {
        return Err("invalid one-hot parameter or forward reference".into());
    }
    mask.iter()
        .position(|&v| v == 1)
        .ok_or_else(|| "missing selection".into())
}

fn dot<const N: usize>(mask: &[u8; N], values: &[i128; N]) -> i128 {
    mask.iter()
        .zip(values)
        .map(|(&weight, &value)| i128::from(weight) * value)
        .sum()
}

impl Model {
    pub fn load(path: &str) -> Result<Self> {
        let model: Self = serde_json::from_reader(std::fs::File::open(path)?)?;
        model.validate()?;
        Ok(model)
    }

    pub fn validate(&self) -> Result<()> {
        if self.format != 1 {
            return Err("unknown checkpoint format".into());
        }
        let mut boolean = [false; WIDTH];
        for (index, gate) in self.gates.iter().enumerate() {
            let operation = selected_index(&gate.operation, OPS)?;
            let left = selected_index(&gate.left, INPUTS + index)?;
            let right = selected_index(&gate.right, INPUTS + index)?;
            if boolean[left] || boolean[right] {
                return Err("operator operands must be integers".into());
            }
            boolean[INPUTS + index] = (4..=6).contains(&operation);
        }
        if !boolean[selected_index(&self.accept, WIDTH)?]
            || boolean[selected_index(&self.next_state, WIDTH)?]
        {
            return Err("model outputs must be a Boolean guard and an integer state".into());
        }
        Ok(())
    }

    /// Every checkpoint runs this same graph. The bank computes all operators;
    /// one-hot parameters select operands and results inside the forward.
    pub fn forward(&self, input: Input) -> Result<Effect> {
        let mut lanes = [0i128; WIDTH];
        for (target, value) in lanes.iter_mut().zip(input.lanes()) {
            *target = i128::from(value);
        }
        for (index, gate) in self.gates.iter().enumerate() {
            let a = dot(&gate.left, &lanes);
            let b = dot(&gate.right, &lanes);
            let bank = [
                a + b,
                a - b,
                a.min(b),
                a.max(b),
                i128::from(a < b),
                i128::from(a <= b),
                i128::from(a == b),
                a,
            ];
            lanes[INPUTS + index] = dot(&gate.operation, &bank);
        }
        let accept = dot(&self.accept, &lanes);
        if accept != 0 && accept != 1 {
            return Err("model acceptance output is not Boolean".into());
        }
        // A rejection retains the state, even when the unused candidate overflows i64.
        let state = accept * dot(&self.next_state, &lanes) + (1 - accept) * i128::from(input.state);
        Ok(Effect {
            accepted: accept == 1,
            state: state.try_into()?,
        })
    }
}
