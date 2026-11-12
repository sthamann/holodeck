//! Bounded synthesis and active queries. This executable is separate from inference.
use holodeck_model_core::{one_hot, Gate, Input, Model, Observation, Result, CELLS, INPUTS};
use serde::Serialize;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

#[derive(Clone, Debug, Eq, Hash, PartialEq, Serialize)]
enum Expr {
    Read(usize),
    Apply(usize, Box<Expr>, Box<Expr>),
}

impl Expr {
    fn apply(op: usize, left: &Self, right: &Self) -> Self {
        Self::Apply(op, Box::new(left.clone()), Box::new(right.clone()))
    }

    fn eval(&self, inputs: &[i64; INPUTS]) -> i64 {
        match self {
            Self::Read(index) => inputs[*index],
            Self::Apply(op, left, right) => {
                let a = left.eval(inputs);
                let b = right.eval(inputs);
                // Deliberately independent of the model forward, on the small teaching domain.
                match op {
                    0 => a + b,
                    1 => a - b,
                    2 => a.min(b),
                    3 => a.max(b),
                    4 => i64::from(a < b),
                    5 => i64::from(a <= b),
                    6 => i64::from(a == b),
                    _ => unreachable!("grammar operation"),
                }
            }
        }
    }
}

fn grammar() -> (Vec<Expr>, Vec<Expr>) {
    let terminals: Vec<_> = (0..INPUTS).map(Expr::Read).collect();
    let mut numeric = vec![terminals];
    let mut boolean = Vec::new();
    // Node budgets are 1, 3 and 5. Numeric subtrees can be reused in guards.
    for size in 1..=2 {
        let mut next = Vec::new();
        for left_size in 0..size {
            let right_size = size - 1 - left_size;
            for left in &numeric[left_size] {
                for right in &numeric[right_size] {
                    next.extend((0..4).map(|op| Expr::apply(op, left, right)));
                    boolean.extend((4..7).map(|op| Expr::apply(op, left, right)));
                }
            }
        }
        numeric.push(next);
    }
    (numeric.into_iter().flatten().collect(), boolean)
}

fn grid() -> Vec<Input> {
    let mut rows = Vec::new();
    for state in -4..=4 {
        for event in -4..=4 {
            for config in -4..=4 {
                rows.push(Input {
                    state,
                    event,
                    config,
                });
            }
        }
    }
    rows
}

struct Oracle {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
}

impl Oracle {
    fn start(command: &str, args: &[String]) -> Result<Self> {
        let mut child = Command::new(command)
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()?;
        let input = child.stdin.take().ok_or("missing teacher stdin")?;
        let output = BufReader::new(child.stdout.take().ok_or("missing teacher stdout")?);
        Ok(Self {
            child,
            input,
            output,
        })
    }

    fn observe(&mut self, input: Input) -> Result<Observation> {
        writeln!(self.input, "{}", serde_json::to_string(&input)?)?;
        let mut line = String::new();
        if self.output.read_line(&mut line)? == 0 {
            return Err("teacher stopped before answering".into());
        }
        let effect: holodeck_model_core::Effect = serde_json::from_str(&line)?;
        if !effect.accepted && effect.state != input.state {
            return Err("teacher refused the event but changed state".into());
        }
        Ok(Observation { input, effect })
    }
}

impl Drop for Oracle {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn compact(candidates: Vec<Expr>, inputs: &[Input]) -> Vec<(Expr, Vec<i64>)> {
    let mut seen = std::collections::HashSet::new();
    let mut unique = Vec::new();
    for candidate in candidates {
        let values: Vec<_> = inputs
            .iter()
            .map(|input| candidate.eval(&input.lanes()))
            .collect();
        if seen.insert(values.clone()) {
            unique.push((candidate, values));
        }
    }
    unique
}

type Candidate = (Expr, Vec<i64>);

fn best_question(candidates: &[Candidate], rows: impl Iterator<Item = usize>) -> Option<usize> {
    rows.filter_map(|index| {
        let mut counts = HashMap::new();
        for (_, values) in candidates {
            *counts.entry(values[index]).or_insert(0) += 1;
        }
        let largest = counts.values().max().copied()?;
        let eliminated = candidates.len() - largest;
        (eliminated > 0).then_some((eliminated, std::cmp::Reverse(index)))
    })
    .max()
    .map(|(_, std::cmp::Reverse(index))| index)
}

fn compile(guard: &Expr, next: &Expr) -> Result<(Model, usize)> {
    fn add(expr: &Expr, gates: &mut Vec<Gate>, slots: &mut HashMap<Expr, usize>) -> Result<usize> {
        if let Expr::Read(index) = expr {
            return Ok(*index);
        }
        if let Some(&index) = slots.get(expr) {
            return Ok(index);
        }
        let Expr::Apply(op, left, right) = expr else {
            unreachable!()
        };
        let left = add(left, gates, slots)?;
        let right = add(right, gates, slots)?;
        if gates.len() == CELLS {
            return Err("model capacity exceeded".into());
        }
        let index = INPUTS + gates.len();
        gates.push(Gate {
            operation: one_hot(*op),
            left: one_hot(left),
            right: one_hot(right),
        });
        slots.insert(expr.clone(), index);
        Ok(index)
    }

    let mut gates = Vec::new();
    let mut slots = HashMap::new();
    let accept = add(guard, &mut gates, &mut slots)?;
    let next_state = add(next, &mut gates, &mut slots)?;
    let used_cells = gates.len();
    gates.resize_with(CELLS, || Gate {
        operation: one_hot(7),
        left: one_hot(3),
        right: one_hot(3),
    });
    let model = Model {
        format: 1,
        gates: gates.try_into().map_err(|_| "wrong gate count")?,
        accept: one_hot(accept),
        next_state: one_hot(next_state),
    };
    model.validate()?;
    Ok((model, used_cells))
}

fn fit(mut oracle: Oracle) -> Result<(Model, serde_json::Value)> {
    const QUERY_LIMIT: usize = 48;
    let inputs = grid();
    let (numeric, boolean) = grammar();
    let mut guards = compact(boolean, &inputs);
    let mut updates = compact(numeric, &inputs);
    let mut rows = vec![None; inputs.len()];
    let mut trace = Vec::new();

    // Seed with varied inputs, all inside the query pool.
    let seeds = [(0, 0, 2), (3, 1, 2), (-2, 1, 3), (1, -2, 3)];
    for (state, event, config) in seeds {
        let index = inputs
            .iter()
            .position(|row| {
                *row == Input {
                    state,
                    event,
                    config,
                }
            })
            .ok_or("seed outside teaching pool")?;
        let row = oracle.observe(inputs[index])?;
        rows[index] = Some(row);
        trace.push(row);
    }
    loop {
        guards.retain(|(_, values)| {
            rows.iter()
                .zip(values)
                .all(|(row, &value)| row.is_none_or(|r| value == i64::from(r.effect.accepted)))
        });
        updates.retain(|(_, values)| {
            rows.iter().zip(values).all(|(row, &value)| {
                row.is_none_or(|r| !r.effect.accepted || value == r.effect.state)
            })
        });
        let (guard, allowed) = guards.first().ok_or("no guard fits the observations")?;
        let (update, _) = updates.first().ok_or("no update fits the observations")?;
        let unknown = rows
            .iter()
            .enumerate()
            .filter_map(|(i, value)| value.is_none().then_some(i));
        let question = best_question(&guards, unknown.clone())
            .or_else(|| best_question(&updates, unknown.filter(|&i| allowed[i] == 1)));
        let Some(index) = question else {
            if !trace.iter().any(|r| r.effect.accepted) || !trace.iter().any(|r| !r.effect.accepted)
            {
                return Err(
                    "teaching evidence must cover both accepted and rejected actions".into(),
                );
            }
            let (model, used_cells) = compile(guard, update)?;
            for row in &trace {
                if model.forward(row.input)? != row.effect {
                    return Err("compiled forward diverges from teaching data".into());
                }
            }
            return Ok((
                model,
                serde_json::json!({
                    "queries": trace.len(),
                    "active_queries": trace.len() - seeds.len(),
                    "guard_classes_on_query_grid": guards.len(),
                    "update_classes_before_restricting_to_accepts": updates.len(),
                    "query_grid_size": inputs.len(),
                    "used_cells": used_cells,
                    "observations": trace,
                    "audit_guard": guard,
                    "audit_update": update
                }),
            ));
        };
        if trace.len() == QUERY_LIMIT {
            return Err("query budget exhausted with distinguishable candidates".into());
        }
        let row = oracle.observe(inputs[index])?;
        rows[index] = Some(row);
        trace.push(row);
    }
}

fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let [checkpoint, report, teacher, teacher_args @ ..] = args.as_slice() else {
        return Err("usage: learn CHECKPOINT REPORT TEACHER [ARGS...]".into());
    };
    let (model, evidence) = fit(Oracle::start(teacher, teacher_args)?)?;
    serde_json::to_writer(std::fs::File::create(checkpoint)?, &model)?;
    serde_json::to_writer_pretty(std::fs::File::create(report)?, &evidence)?;
    println!(
        "{} teacher queries, {} active cells",
        evidence["queries"], evidence["used_cells"]
    );
    Ok(())
}
