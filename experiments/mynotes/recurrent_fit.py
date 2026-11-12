"""Fit the dense weights consumed by deck/kernel.mjs's categorical recurrence."""
import torch
import torch.nn.functional as F


def fit_recurrent(transitions, state_count, input_size, readouts, constants):
    torch.set_num_threads(1)
    torch.manual_seed(25)
    states, tokens, labels = (torch.tensor(values) for values in zip(*transitions))
    encoded = torch.cat((F.one_hot(states, state_count), F.one_hot(tokens, input_size)), dim=1).float()
    model = torch.nn.Sequential(
        torch.nn.Linear(state_count + input_size, 48),
        torch.nn.ReLU(),
        torch.nn.Linear(48, state_count),
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=0.018)
    for epoch in range(400):
        optimizer.zero_grad()
        predicted = model(encoded)
        loss = F.cross_entropy(predicted, labels)
        loss.backward()
        optimizer.step()
        if loss.item() < 0.004 and torch.equal(predicted.argmax(1), labels):
            break
    assert torch.equal(model(encoded).argmax(1), labels), "Recurrent model failed observed transitions"

    heads = []
    for states, classes, targets in readouts:
        terminal = F.one_hot(torch.tensor(states), state_count).float()
        layer = torch.nn.Linear(state_count, len(classes), bias=False)
        opt = torch.optim.Adam(layer.parameters(), lr=0.05)
        expected = torch.tensor(targets)
        for _ in range(160):
            opt.zero_grad()
            loss = F.cross_entropy(layer(terminal), expected)
            loss.backward()
            opt.step()
        assert torch.equal(layer(terminal).argmax(1), expected)
        heads.append({"labels": classes, "weights": layer.weight.detach().tolist()})

    hidden, _, next_state = model
    return {
        "initial": 0,
        "inputSize": input_size,
        "hiddenBias": hidden.bias.detach().tolist(),
        "stateWeights": hidden.weight.detach()[:, :state_count].tolist(),
        "tokenWeights": hidden.weight.detach()[:, state_count:].tolist(),
        "nextWeights": next_state.weight.detach().tolist(),
        "nextBias": next_state.bias.detach().tolist(),
        "heads": heads,
        "constants": constants,
    }, epoch + 1
