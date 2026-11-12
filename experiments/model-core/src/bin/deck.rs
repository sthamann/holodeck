use holodeck_model_core::{Effect, Input, Model, Result};
use rusqlite::{params, Connection, TransactionBehavior};
use serde::{Deserialize, Serialize};
use std::io::{self, BufRead};

#[derive(Debug, Deserialize, Eq, PartialEq)]
struct Event {
    id: String,
    revision: i64,
    event: i64,
    config: i64,
}

#[derive(Debug, Deserialize, Eq, PartialEq, Serialize)]
struct Receipt {
    revision: i64,
    effect: Effect,
}

fn apply(db: &mut Connection, model: &Model, event: &Event) -> Result<Receipt> {
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let prior = tx.query_row(
        "SELECT request_revision, event, config, response FROM receipts WHERE id = ?",
        [&event.id],
        |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get::<_, String>(3)?,
            ))
        },
    );
    match prior {
        Ok((revision, value, config, response)) => {
            if (revision, value, config) != (event.revision, event.event, event.config) {
                return Err("event ID reused with different input".into());
            }
            return Ok(serde_json::from_str(&response)?);
        }
        Err(rusqlite::Error::QueryReturnedNoRows) => {}
        Err(error) => return Err(error.into()),
    }
    let (state, revision): (i64, i64) = tx.query_row(
        "SELECT value, revision FROM state WHERE id = 1",
        [],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    if revision != event.revision {
        return Err("revision conflict".into());
    }
    let effect = model.forward(Input {
        state,
        event: event.event,
        config: event.config,
    })?;
    let receipt = Receipt {
        revision: revision.checked_add(1).ok_or("revision overflow")?,
        effect,
    };
    tx.execute(
        "UPDATE state SET value = ?, revision = ? WHERE id = 1",
        params![effect.state, receipt.revision],
    )?;
    tx.execute(
        "INSERT INTO receipts VALUES (?, ?, ?, ?, ?)",
        params![
            event.id,
            event.revision,
            event.event,
            event.config,
            serde_json::to_string(&receipt)?
        ],
    )?;
    tx.commit()?;
    Ok(receipt)
}

fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().collect();
    match args.as_slice() {
        [_, mode, checkpoint] if mode == "forward" => {
            let model = Model::load(checkpoint)?;
            for line in io::stdin().lock().lines() {
                let input = serde_json::from_str(&line?)?;
                println!("{}", serde_json::to_string(&model.forward(input)?)?);
            }
        }
        [_, mode, checkpoint, database, initial] if mode == "apply" => {
            let model = Model::load(checkpoint)?;
            let mut db = Connection::open(database)?;
            db.busy_timeout(std::time::Duration::from_secs(5))?;
            db.execute_batch(
                "PRAGMA journal_mode = WAL;
                 PRAGMA synchronous = FULL;
                 CREATE TABLE IF NOT EXISTS state
                    (id INTEGER PRIMARY KEY, value INTEGER NOT NULL, revision INTEGER NOT NULL);
                 CREATE TABLE IF NOT EXISTS receipts
                    (id TEXT PRIMARY KEY, request_revision INTEGER NOT NULL, event INTEGER NOT NULL,
                     config INTEGER NOT NULL, response TEXT NOT NULL);",
            )?;
            db.execute(
                "INSERT OR IGNORE INTO state VALUES (1, ?, 0)",
                [initial.parse::<i64>()?],
            )?;
            for line in io::stdin().lock().lines() {
                let event = serde_json::from_str(&line?)?;
                match apply(&mut db, &model, &event) {
                    Ok(receipt) => println!("{}", serde_json::to_string(&receipt)?),
                    Err(error) => println!("{}", serde_json::json!({"error": error.to_string()})),
                }
            }
        }
        _ => {
            return Err("usage: deck forward CHECKPOINT | deck apply CHECKPOINT DB INITIAL".into())
        }
    }
    Ok(())
}
