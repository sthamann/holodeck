// Structural compiler. Candidate text is parsed as data and never evaluated.
const fs = require('node:fs');
const acorn = require(process.env.HOLODECK_ACORN || 'acorn');
const [candidate, output, projection] = process.argv.slice(2);

async function compile() {
  const { operations } = await import('../deck/kernel.mjs');
  const base = process.env.HOLODECK_IMPORTS
    ? JSON.parse(fs.readFileSync(process.env.HOLODECK_IMPORTS, 'utf8')) : null;
  const imported = { ...base?.routines };
  for (const [alias, name] of Object.entries(JSON.parse(process.env.HOLODECK_ALIASES || '{}'))) {
    if (!imported[name]) throw new Error('Missing imported routine ' + name);
    imported[alias] = imported[name];
  }
  const roots = [candidate, projection].filter(Boolean).flatMap((file) =>
    acorn.parse(fs.readFileSync(file, 'utf8'), { ecmaVersion: 'latest', locations: true }).body);
  const functions = new Map();
  const routeNodes = [];
  const fail = (node, reason) => { throw new Error(`${reason} near line ${node.loc?.start.line ?? '?'}`); };
  const register = (name, declaration) => {
    if (functions.has(name)) fail(declaration, 'Duplicate function ' + name);
    functions.set(name, declaration);
  };
  for (const root of roots) {
    if (root.type === 'FunctionDeclaration' && root.id && !root.async && !root.generator) {
      register(root.id.name, root);
    } else if (root.type === 'VariableDeclaration' && root.declarations.every(({ id, init }) =>
      id.type === 'Identifier' && init?.type === 'ArrowFunctionExpression' && !init.async)) {
      for (const { id, init } of root.declarations) register(id.name, init);
    } else if (root.type === 'ExpressionStatement' && root.expression.type === 'CallExpression' &&
        root.expression.callee.name === 'route') routeNodes.push(root.expression.arguments);
    else if (root.type !== 'EmptyStatement') fail(root, 'Expected a function or route declaration, found ' + root.type);
  }
  const rows = [...(base?.parameters ?? [])];
  const seen = new Map(rows.map((row, i) => [JSON.stringify(row), i]));
  const emit = (operator, ...operands) => {
    const opcode = operations.indexOf(operator);
    if (opcode < 0) throw new Error('Unknown operator ' + operator);
    const row = [opcode, ...operands];
    const key = JSON.stringify(row);
    if (!seen.has(key)) { seen.set(key, rows.length); rows.push(row); }
    return seen.get(key);
  };
  const lit = (x) => emit('literal', x);
  const ref = (x) => emit('ref', x);
  const obj = (values) => emit('record', ...Object.entries(values));
  const emptyArray = lit([]);
  const emptyRecord = lit({});
  const nil = lit(null);
  const globals = new Map(['state', 'body', 'params', 'headers', 'query', 'now'].map((name) => [name, name]));
  const primitiveNames = new Map(operations.slice(11).map((op) => [op, op]));
  primitiveNames.set('withKey', 'with');
  primitiveNames.set('equals', 'eq');
  primitiveNames.set('iso', 'time');
  let ids = 0;
  const local = () => '$' + ++ids;

  function seq(nodes, scope, end = () => nil) {
    if (!nodes.length) return end(scope);
    const [first, ...rest] = nodes;
    switch (first.type) {
      case 'EmptyStatement': return seq(rest, scope, end);
      case 'BlockStatement': return seq(first.body, new Map(scope), (after) =>
        seq(rest, new Map([...scope].map(([name, key]) => [name, after.get(name) ?? key])), end));
      case 'ReturnStatement': return first.argument ? expr(first.argument, scope) : nil;
      case 'IfStatement': {
        const next = (after = scope) => seq(rest, after, end);
        return emit('if', expr(first.test, scope), seq([first.consequent], new Map(scope), next),
          first.alternate ? seq([first.alternate], new Map(scope), next) : next());
      }
      case 'VariableDeclaration': {
        const next = (index, current) => {
          if (index >= first.declarations.length) return seq(rest, current, end);
          const decl = first.declarations[index];
          if (decl.id.type !== 'Identifier' || !decl.init) fail(decl, 'Only initialized local names are supported');
          if (decl.init.type === 'ArrowFunctionExpression') {
            const closure = { node: decl.init, scope: current };
            return next(index + 1, new Map(current).set(decl.id.name, closure));
          }
          const name = local();
          return emit('let', name, expr(decl.init, current),
            next(index + 1, new Map(current).set(decl.id.name, name)));
        };
        return next(0, scope);
      }
      case 'ExpressionStatement': {
        const n = first.expression;
        const callee = n.callee;
        if (n.type === 'CallExpression' && callee.type === 'MemberExpression' && callee.object.type === 'Identifier' &&
            !callee.computed && callee.property.name === 'push' && typeof scope.get(callee.object.name) === 'string') {
          const variable = callee.object.name;
          const value = emit('concat', ref(scope.get(variable)), emit('array', ...n.arguments.map((a) => expr(a, scope))));
          const key = local();
          return emit('let', key, value, seq(rest, new Map(scope).set(variable, key), end));
        }
        return fail(first, 'Unsupported expression statement');
      }
      default: return fail(first, 'Unsupported statement ' + first.type);
    }
  }

  function callback(node, scope, slots) {
    if (node?.type !== 'ArrowFunctionExpression' || node.params.some((x) => x.type !== 'Identifier') ||
        node.params.length > slots.length || node.async) return fail(node, 'Expected a simple arrow callback');
    const inner = new Map(scope);
    const locals = node.params.map((param) => { const key = local(); inner.set(param.name, key); return key; });
    let result = node.body.type === 'BlockStatement' ? seq(node.body.body, inner) : expr(node.body, inner);
    for (let i = locals.length - 1; i >= 0; i--) result = emit('let', locals[i], ref(slots[i]), result);
    return result;
  }

  function call(node, scope) {
    const { callee, arguments: args } = node;
    if (callee.type === 'MemberExpression' && !callee.computed) {
      const name = callee.property.name;
      const receiver = expr(callee.object, scope);
      if (name === 'map' || name === 'filter' || name === 'every' || name === 'some') {
        const slot = local();
        const mapped = callback(args[0], scope, [slot, 'index']);
        if (name === 'map' || name === 'filter') return emit(name, receiver, slot, mapped);
        const matching = emit('filter', receiver, slot, mapped);
        return name === 'every' ? emit('eq', emit('length', matching), emit('length', receiver))
          : emit('lt', lit(0), emit('length', matching));
      }
      const methods = { includes: 'includes', toLowerCase: 'lower', trim: 'trim', startsWith: 'starts' };
      if (methods[name]) return emit(methods[name], receiver, ...args.map((arg) => expr(arg, scope)));
    }
    if (callee.type !== 'Identifier') return fail(node, 'Only named calls are supported');
    const name = callee.name;
    if (scope.get(name)?.node) {
      const closure = scope.get(name);
      const inner = new Map(closure.scope);
      const bindings = closure.node.params.map((parameter, i) => {
        if (parameter.type !== 'Identifier') fail(parameter, 'Expected simple closure parameter');
        const key = local();
        inner.set(parameter.name, key);
        return [key, args[i] ? expr(args[i], scope) : nil];
      });
      const value = closure.node.body.type === 'BlockStatement'
        ? seq(closure.node.body.body, inner) : expr(closure.node.body, inner);
      return bindings.reduceRight((tail, [key, row]) => emit('let', key, row, tail), value);
    }
    const item = local();
    if (name === 'map' || name === 'filter') {
      return emit(name, expr(args[0], scope), item, callback(args[1], scope, [item, 'index']));
    }
    if (name === 'fold') {
      return emit('fold', expr(args[0], scope), expr(args[1], scope), item,
        callback(args[2], scope, ['acc', item, 'index']));
    }
    if (name === 'sortBy') {
      return emit('sort', expr(args[0], scope), item, callback(args[1], scope, [item]),
        args[2] ? expr(args[2], scope) : lit(1));
    }
    const operands = args.map((arg) => expr(arg, scope));
    const [a, b, c, d] = operands;
    if (name === 'respond') return obj({ status: a, body: b ?? nil, effects: c ?? emptyArray, headers: d ?? emptyRecord });
    if (name === 'fail') return obj({ status: a,
      body: emit('if', emit('eq', emit('type', b), lit('string')), obj({ error: b }), b),
      effects: emptyArray, headers: emptyRecord });
    if (name === 'put') return obj({ op: lit('put'), collection: a, key: b, value: c });
    if (name === 'del') return obj({ op: lit('delete'), collection: a, key: b });
    if (functions.has(name) || Object.hasOwn(imported, name)) return emit('call', name, ...operands);
    if (name === 'range' && operands.length === 2) return emit('interval', a, b);
    if (!primitiveNames.has(name)) return fail(node, 'Unknown function ' + name);
    return emit(primitiveNames.get(name), ...operands);
  }

  function expr(node, scope) {
    switch (node.type) {
      case 'Literal':
        if (node.regex || node.bigint) return fail(node, 'Unsupported literal');
        return lit(node.value);
      case 'Identifier':
        if (node.name === 'undefined') return nil;
        if (node.name === 'Infinity') return lit(Number.MAX_SAFE_INTEGER);
        if (!scope.has(node.name)) return fail(node, 'Unknown input ' + node.name);
        return ref(scope.get(node.name));
      case 'ChainExpression': return expr(node.expression, scope);
      case 'MemberExpression':
        if (!node.computed && node.property.name === 'length') return emit('length', expr(node.object, scope));
        return emit('get', expr(node.object, scope), node.computed ? expr(node.property, scope) : lit(node.property.name));
      case 'ArrayExpression': return emit('array', ...node.elements.map((x) => x ? expr(x, scope) : nil));
      case 'ObjectExpression': {
        let result = emptyRecord;
        for (const entry of node.properties) {
          if (entry.type === 'SpreadElement') { result = emit('merge', result, expr(entry.argument, scope)); continue; }
          if (entry.type !== 'Property' || entry.kind !== 'init' || entry.computed || entry.method) {
            return fail(entry, 'Only static object keys are supported');
          }
          if (entry.value.type === 'Identifier' && entry.value.name === 'undefined') {
            result = emit('without', result, lit(entry.key.name ?? entry.key.value));
          } else {
            result = emit('with', result, lit(entry.key.name ?? entry.key.value), expr(entry.value, scope));
          }
        }
        return result;
      }
      case 'TemplateLiteral': {
        const parts = [];
        node.quasis.forEach((part, i) => {
          parts.push(lit(part.value.cooked));
          if (i < node.expressions.length) parts.push(expr(node.expressions[i], scope));
        });
        return emit('join', emit('array', ...parts), lit(''));
      }
      case 'ConditionalExpression': return emit('if', expr(node.test, scope),
        expr(node.consequent, scope), expr(node.alternate, scope));
      case 'LogicalExpression': {
        const a = expr(node.left, scope);
        const b = expr(node.right, scope);
        if (node.operator === '&&') return emit('if', a, b, a);
        if (node.operator === '||') return emit('if', a, a, b);
        if (node.operator === '??') return emit('coalesce', a, b);
        return fail(node, 'Unsupported logical operator');
      }
      case 'UnaryExpression': {
        const a = expr(node.argument, scope);
        if (node.operator === '!') return emit('not', a);
        if (node.operator === '-') return emit('sub', lit(0), a);
        if (node.operator === 'typeof') return emit('type', a);
        return fail(node, 'Unsupported unary operator');
      }
      case 'BinaryExpression': {
        const a = expr(node.left, scope);
        const b = expr(node.right, scope);
        const direct = { '+': 'add', '-': 'sub', '<': 'lt', '<=': 'lte', '==': 'eq', '===': 'eq' };
        if (direct[node.operator]) return emit(direct[node.operator], a, b);
        if (node.operator === '>') return emit('lt', b, a);
        if (node.operator === '>=') return emit('lte', b, a);
        if (node.operator === '!=' || node.operator === '!==') return emit('not', emit('eq', a, b));
        return fail(node, 'Unsupported binary operator ' + node.operator);
      }
      case 'CallExpression': return call(node, scope);
      default: return fail(node, 'Unsupported expression ' + node.type);
    }
  }

  const routines = { ...imported };
  for (const [name, declaration] of functions) {
    const scope = new Map(globals);
    const inputs = declaration.params.map((param) => {
      if (param.type !== 'Identifier') return fail(param, 'Expected a simple parameter name');
      const key = local();
      scope.set(param.name, key);
      return key;
    });
    const entry = declaration.body.type === 'BlockStatement'
      ? seq(declaration.body.body, scope) : expr(declaration.body, scope);
    routines[name] = { inputs, entry };
  }
  const newRoutes = routeNodes.map(([method, path, handler]) => {
    if (typeof method?.value !== 'string' || typeof path?.value !== 'string' || !routines[handler?.name] ||
      routines[handler.name].inputs.length !== 0) throw new Error('Invalid route binding');
    return { method: method.value, path: path.value, entry: emit('call', handler.name) };
  });
  const routes = [...newRoutes, ...(base?.routes ?? []).filter((route) =>
    !newRoutes.some(({ method, path }) => route.method === method && route.path === path))];
  fs.writeFileSync(output, JSON.stringify({ format: 1, routines, routes, parameters: rows }) + '\n');
  console.log(`Lowered ${rows.length} rows, ${routes.length} routes, ${functions.size} routines`);
}
compile().catch((error) => { console.error(error.message); process.exitCode = 1; });
