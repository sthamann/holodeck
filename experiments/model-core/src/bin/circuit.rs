//! Exact fixed-shape forward for compiled transition parameters.
use holodeck_model_core::Result;
use serde::Deserialize;
use std::io::{self, BufRead};

const INPUTS: usize = 9;
const CELLS: usize = 14;
const LANES: usize = INPUTS + CELLS;
const OPS: usize = 6;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Gate {
    operation: [u8; OPS],
    left: [u8; LANES],
    right: [u8; LANES],
    alternate: [u8; LANES],
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Circuit {
    gates: [Gate; CELLS],
    outputs: [[u8; LANES]; 4],
}

fn valid(mask: &[u8], available: usize) -> bool {
    mask.iter().all(|&w| w <= 1)
        && mask.iter().filter(|&&w| w == 1).count() == 1
        && mask[available..].iter().all(|&w| w == 0)
}

fn dot(mask: &[u8], lanes: &[i128]) -> i128 {
    mask.iter()
        .zip(lanes)
        .map(|(&w, &v)| i128::from(w) * v)
        .sum()
}

impl Circuit {
    fn validate(&self) -> Result<()> {
        for (i, gate) in self.gates.iter().enumerate() {
            if !valid(&gate.operation, OPS)
                || [&gate.left, &gate.right, &gate.alternate]
                    .iter()
                    .any(|operand| !valid(*operand, INPUTS + i))
            {
                return Err("invalid parameter or forward reference".into());
            }
        }
        if self.outputs.iter().any(|output| !valid(output, LANES)) {
            return Err("invalid output binding".into());
        }
        Ok(())
    }

    fn forward(&self, input: [i64; INPUTS]) -> Result<[i64; 4]> {
        let mut lanes = [0i128; LANES];
        for (lane, value) in lanes.iter_mut().zip(input) {
            *lane = i128::from(value);
        }
        for (i, gate) in self.gates.iter().enumerate() {
            let a = dot(&gate.left, &lanes);
            let b = dot(&gate.right, &lanes);
            let c = dot(&gate.alternate, &lanes);
            let bank = [
                i128::from(a == b),
                a + b,
                i128::from(a < b),
                i128::from(a <= b),
                if a == 0 { c } else { b },
                a,
            ];
            lanes[INPUTS + i] = dot(&gate.operation, &bank);
        }
        let mut output = [0; 4];
        for (target, weights) in output.iter_mut().zip(&self.outputs) {
            *target = dot(weights, &lanes).try_into()?;
        }
        Ok(output)
    }
}

fn main() -> Result<()> {
    let path = std::env::args().nth(1).ok_or("usage: circuit CHECKPOINT")?;
    let circuit: Circuit = serde_json::from_reader(std::fs::File::open(path)?)?;
    circuit.validate()?;
    for line in io::stdin().lock().lines() {
        let input = serde_json::from_str(&line?)?;
        println!("{}", serde_json::to_string(&circuit.forward(input)?)?);
    }
    Ok(())
}
