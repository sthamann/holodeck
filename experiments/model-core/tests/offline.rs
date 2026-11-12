use holodeck_model_core::{Effect, Input, Model, Observation};
use serde::{de::DeserializeOwned, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};

const TEACHER: &str = env!("CARGO_BIN_EXE_teacher");
const LEARNER: &str = env!("CARGO_BIN_EXE_learn");
const DECK: &str = env!("CARGO_BIN_EXE_deck");

struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Self {
        static NEXT: AtomicUsize = AtomicUsize::new(0);
        let path = std::env::temp_dir().join(format!(
            "holodeck-native-core-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn checked(output: Output) -> Output {
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    output
}

fn run_rows<T: Serialize, U: DeserializeOwned>(mut command: Command, rows: &[T]) -> Vec<U> {
    let mut encoded = Vec::new();
    for row in rows {
        serde_json::to_writer(&mut encoded, row).unwrap();
        encoded.push(b'\n');
    }
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let writer = std::thread::spawn(move || stdin.write_all(&encoded).unwrap());
    let output = checked(child.wait_with_output().unwrap());
    writer.join().unwrap();
    String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

fn predict(deck: &Path, checkpoint: &Path, cases: &[Input]) -> Vec<Effect> {
    let mut runner = Command::new(deck);
    runner.env_clear().arg("forward").arg(checkpoint);
    run_rows(runner, cases)
}

fn learn(policy: &str, checkpoint: &Path, report: &Path) -> Output {
    Command::new(LEARNER)
        .arg(checkpoint)
        .arg(report)
        .args([TEACHER, policy])
        .output()
        .unwrap()
}

fn reference(policy: &str, inputs: &[Input]) -> Vec<Effect> {
    let mut teacher = Command::new(TEACHER);
    teacher.arg(policy);
    run_rows(teacher, inputs)
}

fn holdout() -> Vec<Input> {
    let mut rows = Vec::new();
    for state in -12i64..=12 {
        for event in -12i64..=12 {
            for config in -12i64..=12 {
                if [state, event, config].iter().any(|v| v.abs() > 4) {
                    rows.push(Input {
                        state,
                        event,
                        config,
                    });
                }
            }
        }
    }
    rows.extend((100..228).map(|state| Input {
        state,
        event: 47,
        config: state + 47,
    }));
    rows
}

fn compare(left: &Value, right: &Value) -> usize {
    match (left, right) {
        (Value::Array(a), Value::Array(b)) => {
            assert_eq!(a.len(), b.len());
            a.iter().zip(b).map(|(l, r)| compare(l, r)).sum()
        }
        (Value::Object(a), Value::Object(b)) => {
            assert_eq!(a.len(), b.len());
            a.iter().map(|(key, value)| compare(value, &b[key])).sum()
        }
        _ => usize::from(left != right),
    }
}

#[test]
fn isolated_deck_learns_two_behaviors_and_a_teacher_mutation() {
    let output = Scratch::new();
    let offline = output.0.join("offline");
    fs::create_dir(&offline).unwrap();
    let runner = offline.join("deck");
    fs::copy(DECK, &runner).unwrap();
    let mut learned = Vec::new();
    for policy in ["buffer", "strict-buffer", "signal"] {
        let weights = offline.join(format!("{policy}.json"));
        let report = output.0.join(format!("{policy}-report.json"));
        checked(learn(policy, &weights, &report));
        let report: Value = serde_json::from_slice(&fs::read(report).unwrap()).unwrap();
        let rows: Vec<Observation> =
            serde_json::from_value(report["observations"].clone()).unwrap();
        assert!(report["active_queries"].as_u64().unwrap() > 0);
        assert_eq!(report["guard_classes_on_query_grid"], 1);
        assert!(rows.len() < 48);
        learned.push((policy, weights, rows.len()));
    }

    let inputs = holdout();
    // Record labels only once all checkpoints are frozen. Each teacher process
    // exits before the copied inference binary runs.
    let expected: Vec<_> = learned
        .iter()
        .map(|(policy, _, _)| reference(policy, &inputs))
        .collect();
    for ((policy, checkpoint, queries), expected) in learned.iter().zip(&expected) {
        let actual = predict(&runner, checkpoint, &inputs);
        let exact = actual.iter().zip(expected).filter(|(a, b)| a == b).count();
        assert_eq!(actual.len(), inputs.len());
        assert_eq!(exact, inputs.len(), "{policy}");
        println!(
            "{policy}: {queries} teaching queries, {exact}/{} offline",
            inputs.len()
        );
    }

    let old_on_new = predict(&runner, &learned[0].1, &inputs);
    let mismatches = old_on_new
        .iter()
        .zip(&expected[1])
        .filter(|(a, b)| a != b)
        .count();
    assert!(mismatches >= 128);
    let a: Value = serde_json::from_slice(&fs::read(&learned[0].1).unwrap()).unwrap();
    let b: Value = serde_json::from_slice(&fs::read(&learned[1].1).unwrap()).unwrap();
    assert_eq!(compare(&a, &b), 2);
    println!("old weights on mutated teacher: {mismatches} mismatches; two changed entries in one opcode selector");

    let database = offline.join("state.sqlite");
    let apply = |checkpoint: &Path, rows: &[Value]| -> Vec<Value> {
        let mut runtime = Command::new(&runner);
        runtime
            .env_clear()
            .arg("apply")
            .arg(checkpoint)
            .arg(&database)
            .arg("7");
        run_rows(runtime, rows)
    };
    let first = json!({"id":"first","revision":0,"event":3,"config":10});
    let receipt = json!({"revision":1,"effect":{"accepted":true,"state":10}});
    assert_eq!(
        apply(&learned[0].1, std::slice::from_ref(&first)),
        std::slice::from_ref(&receipt)
    );
    let modified = json!({"id":"first","revision":0,"event":4,"config":10});
    let stale = json!({"id":"stale","revision":0,"event":1,"config":20});
    let rejected = json!({"id":"boundary","revision":1,"event":0,"config":10});
    assert_eq!(
        apply(&learned[1].1, &[first, modified, stale, rejected]),
        [
            receipt,
            json!({"error":"event ID reused with different input"}),
            json!({"error":"revision conflict"}),
            json!({"revision":2,"effect":{"accepted":false,"state":10}})
        ]
    );
    let after_restart = json!({"id":"next","revision":2,"event":1,"config":12});
    assert_eq!(
        apply(&learned[1].1, &[after_restart]),
        [json!({"revision":3,"effect":{"accepted":true,"state":11}})]
    );

    // Two competing writes for the same revision get exactly one commit.
    std::thread::scope(|scope| {
        let left = scope.spawn(|| {
            apply(
                &learned[1].1,
                &[json!({"id":"left","revision":3,"event":1,"config":20})],
            )
        });
        let right = scope.spawn(|| {
            apply(
                &learned[1].1,
                &[json!({"id":"right","revision":3,"event":2,"config":20})],
            )
        });
        let outcomes = [left.join().unwrap(), right.join().unwrap()].concat();
        assert_eq!(outcomes.iter().filter(|r| r["revision"] == 4).count(), 1);
        assert_eq!(
            outcomes
                .iter()
                .filter(|r| r["error"] == "revision conflict")
                .count(),
            1
        );
    });

    // A rejected candidate may exceed i64 without contaminating the saved state.
    let overflow = Input {
        state: i64::MAX,
        event: 1,
        config: i64::MAX,
    };
    assert_eq!(
        predict(&runner, &learned[0].1, &[overflow]),
        [Effect {
            accepted: false,
            state: i64::MAX
        }]
    );
    let wrong_type = offline.join("wrong-type.json");
    let mut malformed = Model::load(learned[0].1.to_str().unwrap()).unwrap();
    malformed.accept = holodeck_model_core::one_hot(0);
    fs::write(&wrong_type, serde_json::to_vec(&malformed).unwrap()).unwrap();
    assert!(!Command::new(&runner)
        .arg("forward")
        .arg(wrong_type)
        .output()
        .unwrap()
        .status
        .success());
    let mut malformed = Model::load(learned[0].1.to_str().unwrap()).unwrap();
    malformed.gates[0].left.fill(1);
    assert!(malformed.validate().is_err());
}

#[test]
fn incompatible_teacher_does_not_export_a_model() {
    let output = Scratch::new();
    let weights = output.0.join("weights.json");
    let report = output.0.join("report.json");
    let result = learn("outside-grammar", &weights, &report);
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("no update fits"));
    assert!(!weights.exists());
}
