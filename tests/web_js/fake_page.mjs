// A small fake page for the Dashboard's scripts, shared by the Node tests that run them.
//
// Elements keep their children, attributes and handlers; a part that is no node is written as its
// text, as a browser does (so a missing part reads "null"); and ``el`` and ``fill`` are app.js's own
// helpers as the scripts use them. Nothing here reaches a network or a file.

export const asNode = (part) =>
  part && part.nodeType ? part : { nodeType: 3, textContent: String(part) };

export class Node {
  constructor(tag) {
    this.tag = tag;
    this.nodeType = 1;
    this.children = [];
    this.attrs = {};
    this.handlers = {};
    this.style = { setProperty() {} };
    this.classList = { toggle() {}, add() {}, remove() {}, contains: () => false };
    this.textContent = "";
    this.hidden = false;
    this.isConnected = true;
  }
  append(...kids) { this.children.push(...kids.map(asNode)); }
  replaceChildren(...kids) { this.children = kids.map(asNode); }
  replaceWith() {}
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  addEventListener(type, handler) { (this.handlers[type] = this.handlers[type] || []).push(handler); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  contains() { return false; }
  getBoundingClientRect() { return { x: 0, y: 0, left: 0, top: 0, width: 900, height: 600 }; }
  getComputedTextLength() { return 60; }
  before() {}
  focus() {}
}

export const walk = (node, found = []) => {
  found.push(node);
  for (const child of node.children || []) walk(child, found);
  return found;
};

/** Every piece of text under a node, in order: a text node's, and the text an element was made with. */
export const words = (root) =>
  walk(root)
    .filter((node) => (node.nodeType === 3 || node.textContent) && node.textContent !== "")
    .map((node) => node.textContent);

export const fire = (node, type) =>
  (node.handlers[type] || []).forEach((handler) => handler({ target: node }));

export const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

/** An array or object made in another context, as plain data. */
export const plain = (value) => JSON.parse(JSON.stringify(value));

/** The document, ``el`` and ``fill`` a script is given. */
export function pageHelpers() {
  const text = (value) => ({ nodeType: 3, textContent: value });
  const document = {
    activeElement: null,
    head: new Node("head"),
    createElement(tag) {
      return tag === "template"
        ? { set innerHTML(_) {}, content: { firstElementChild: { namespaceURI: "svg-namespace" } } }
        : new Node(tag);
    },
    createElementNS: (_, tag) => new Node(tag),
    createDocumentFragment: () => new Node("fragment"),
    createTextNode: text,
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  function el(tag, attributes = {}, children = []) {
    const node = new Node(tag);
    for (const [name, value] of Object.entries(attributes)) {
      if (name === "class") node.className = value;
      else if (name === "text") node.textContent = value;
      else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
      else node.setAttribute(name, value);
    }
    for (const child of [].concat(children)) {
      if (child == null) continue;
      node.append(child.nodeType ? child : text(String(child)));
    }
    return node;
  }
  function fill(pane, ...parts) {
    pane.replaceChildren(...parts.flat().filter((part) => part != null && part !== ""));
  }
  return { document, el, fill };
}
