const actionSelector = '#actions button[data-holo-key^="action:hold:"]';
const settleSelector = '#actions button[data-holo-key^="action:settle:"]';

function observe(predicate) {
  if (predicate()) return Promise.resolve();
  return new Promise(resolve => {
    const watcher = new MutationObserver(() => {
      if (!predicate()) return;
      watcher.disconnect();
      resolve();
    });
    watcher.observe(document.body, {subtree: true, childList: true, attributes: true, characterData: true});
  });
}

await observe(() => document.body.dataset.sceneEpoch === "0");
document.querySelector(actionSelector).click();
await observe(() => document.body.dataset.sceneEpoch === "1");
if (document.querySelector("#status").textContent !== "committed" ||
    !document.querySelector(settleSelector) || document.querySelector(actionSelector)) {
  throw Error("browser action or replacement patch did not arrive");
}
document.body.dataset.replay = "passed";
