//! Executable references. Neither the trainer nor the runtime links these rules.
use holodeck_model_core::{Effect, Input, Result};
use std::io::{self, BufRead, Write};

fn observe(policy: &str, input: Input) -> Result<Effect> {
    let Input {
        state,
        event,
        config,
    } = input;
    let (accepted, next) = match policy {
        "buffer" => (state + event <= config, state + event),
        "strict-buffer" => (state + event < config, state + event),
        "outside-grammar" => (state + event <= config, state * event + config),
        "signal" => (state >= event, (state - event).min(config)),
        _ => return Err("unknown teaching policy".into()),
    };
    Ok(Effect {
        accepted,
        state: if accepted { next } else { state },
    })
}

fn main() -> Result<()> {
    let policy = std::env::args().nth(1).ok_or("usage: teacher POLICY")?;
    let mut out = io::stdout().lock();
    for line in io::stdin().lock().lines() {
        let input = serde_json::from_str(&line?)?;
        writeln!(out, "{}", serde_json::to_string(&observe(&policy, input)?)?)?;
        out.flush()?;
    }
    Ok(())
}
