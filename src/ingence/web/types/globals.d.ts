// What graph.js uses from the Dashboard's other scripts and from the vendored drawing library.
//
// Written by hand, beside the generated api.d.ts: the Dashboard's older scripts (app.js,
// review.js, workspace.js) are plain JavaScript loaded before graph.js, and these are the few of
// their globals graph.js calls. The drawing library is declared loosely on purpose: the contract
// worth typing strictly is the data the server sends, which api.d.ts holds.

/** Make an element: attributes (``class``, ``text``, ``on<event>``, any other attribute) and children. */
declare function el(
  tag: string,
  attributes?: { [name: string]: string | ((event: Event) => unknown) },
  children?: Array<Node | string | null | undefined> | Node | string | null | undefined,
): HTMLElement;

/** Replace a pane's contents, leaving out the parts that are null or empty (review.js). */
declare function fill(pane: Element, ...parts: Array<Node | string | null | undefined>): void;

/** Open one of the Dashboard's views by name (app.js). */
declare function showView(view: string): void;

/** Say an error in the list pane (app.js). */
declare function reportError(error: unknown): void;

/** One helper for every page's loading, error and empty states (app.js). */
declare const states: {
  loading(pane: Element, words?: string): void;
  /** The error state: names a failed request's method, route, status and time; Try again; Report a problem. */
  failed(pane: Element | null, error: unknown, retry?: (() => void) | null, options?: { into?: boolean }): HTMLElement;
  empty(pane: Element, words: string): void;
  /** One drawn state per view: eyebrow, dot, sentence, a line or two, actions. */
  say(pane: Element | null, state: {
    eyebrow?: string; dot?: 'empty' | 'census' | 'loading' | 'problem'; sentence: string;
    body?: string | string[]; role?: string; into?: boolean;
    actions?: Array<{ label: string; onclick?: (event: Event) => unknown; href?: string }>;
  }): HTMLElement;
};


/** A pane's turns: the last read asked for is the one a pane draws (app.js). */
declare const turns: { claim(pane: Element): number; owns(pane: Element, turn: number): boolean };

/** The object under investigation, kept across pages and reloads (app.js). */
declare function setFocus(focus: { kind: string; id: string; label?: string }): void;

/** A stored value (a state, a kind) in plain words, from one table (app.js). */
declare function plainValue(raw: unknown): string;

/** A guarded write to the one route that writes, or a workspace route (app.js). */
declare function write(path: string, body: { [name: string]: unknown }): Promise<any>;

/** Ask once who is deciding on this machine; false if nobody said (review.js). */
declare function askActor(): Promise<boolean>;

/** Who is deciding on this machine (review.js). */
declare const me: { actor: string };

/** The Dashboard's dialogs: labelled, focus kept inside, Escape closes (dialogs.js). */
declare const dialogs: Dialogs;

interface Dialogs {
  open(options: { title: string; text?: string; eyebrow?: string;
    build: (finish: (value: unknown) => void) => Node | string | null | undefined
      | Array<Node | string | null | undefined>;
    cancelled?: unknown; className?: string }): Promise<any>;
  ask(options: { title: string; text?: string; label?: string; value?: string; placeholder?: string;
    submit?: string; validate?: ((typed: string) => string) | null; allowEmpty?: boolean;
    multiline?: boolean }): Promise<string | null>;
  tell(options: { title?: string; text: string }): Promise<void>;
  choose(options: { title: string; text: string; details?: Node[]; yes?: string; no?: string }): Promise<boolean>;
  failed(error: unknown, title?: string): Promise<void>;
}

/** The Dashboard's list pane, where the Graph page draws (app.js). */
declare const listPane: HTMLElement;

/** Read a route and return its JSON; throws an error that names the route and the status (app.js). */
declare function api(path: string): Promise<any>;

/** Open one of the Dashboard's views and draw it (app.js). */
declare function navigate(view: string, options?: { push?: boolean; focus?: unknown }): Promise<unknown>;

/** Which view is being shown (app.js). Experiments and Words both read it. */
declare const state: { view: string; project: any };

/** The draw function of each view, by name; a script adds its own view here (app.js). */
declare const VIEWS: { [view: string]: () => Promise<unknown> | void };

/** The views that take the whole width, with nothing in the pane beside them (app.js). */
declare const SINGLE_VIEWS: Set<string>;

/** The Proposed pairs page's filters (review.js), set when a proposed line is taken there. */
declare const sheetState: { top: string; rule: string; kind?: string | null };

/** The one evidence inspector (inspector.js). */
interface Inspector {
  pane(): HTMLElement;
  showEntry(id: string, where?: HTMLElement, options?: {
    facts?: Array<[string, string]>; actions?: HTMLElement[]; back?: (() => void) | null;
    backLabel?: string; keepFocus?: boolean }): Promise<void>;
  showWord(word: string, where?: HTMLElement, options?: { back?: (() => void) | null; backLabel?: string }): Promise<void>;
  showEdge(id: string, run: string, where?: HTMLElement): Promise<void>;
  wordLink(word: string, label?: string): HTMLElement;
  filesOf(files: Array<{ entry_id?: string; id?: string; path?: string; name?: string }>, back: () => void): HTMLElement;
  entryBlocks(found: unknown, target: HTMLElement, options: unknown): Array<Node | null>;
  edgeBlocks(detail: unknown): Array<Node | null>;
}

/** Cytoscape.js, vendored under vendor/cytoscape (typed loosely: see above). */
declare function cytoscape(options: { [key: string]: unknown }): CytoscapeCore;

interface CytoscapeElement {
  id(): string;
  data(name: string): unknown;
  position(): { x: number; y: number };
  addClass(name: string): CytoscapeElement;
  removeClass(name: string): CytoscapeElement;
}

interface CytoscapeCollection {
  length: number;
  forEach(each: (element: CytoscapeElement) => void): void;
  removeClass(name: string): CytoscapeCollection;
  addClass(name: string): CytoscapeCollection;
  /** The compound parent of each element (an empty collection where there is none). */
  parent(): CytoscapeCollection;
  /** The box the collection fills, in the drawing's own coordinates. */
  boundingBox(options: { [key: string]: unknown }): { x1: number; y1: number; x2: number; y2: number };
  /** The same box in pixels on the page. */
  renderedBoundingBox(options: { [key: string]: unknown }): { x1: number; y1: number; x2: number; y2: number };
  map<T>(each: (element: CytoscapeElement & { renderedBoundingBox(options: { [key: string]: unknown }): { x1: number; y1: number; x2: number; y2: number } }) => T): T[];
}

interface CytoscapeCore {
  on(events: string, selector: string, handler: (event: { target: CytoscapeElement }) => void): void;
  on(events: string, handler: (event: { target: unknown }) => void): void;
  getElementById(id: string): CytoscapeElement & { length: number };
  elements(selector?: string): CytoscapeCollection;
  nodes(selector?: string): CytoscapeCollection;
  fit(elements?: unknown, padding?: number): void;
  container(): HTMLElement | null;
  center(elements?: unknown): void;
  destroy(): void;
  resize(): void;
  zoom(): number;
  zoom(level: number): void;
  pan(position: { x: number; y: number }): void;
  animate(to: { [key: string]: unknown }, options: { duration: number }): void;
}

interface Window {
  /** The Graph page's entry point, called by app.js's showGraph. */
  showGraphPage: () => Promise<void>;
  /** Open the Graph page centred on one entry: the View in Graph hook (graph.js). */
  openInGraph: (entryId: string) => void;
  /** A "View in Graph" button for an entry, for Explore, search results and the inspector. */
  graphButton: (entryId: string, label?: string) => HTMLElement;
  /** The one evidence inspector (inspector.js). */
  inspector: Inspector;
  /** The Dashboard's dialogs (dialogs.js). */
  dialogs: Dialogs;
  /** The footer's email draft, exposed so a test can give it a hostile input (report.js). */
  /** The engine's version answer, read once for the page (footer.js); null if it cannot be read. */
  ingenceVersion: () => Promise<any>;
  ingenceReport: {
    addressFor: (facts: object) => string;
    bodyText: (facts: object) => string;
    detailsText: (facts: object) => string;
    settingLines: (settings: any[] | null | undefined, listAtMost?: number) => string[];
    reportFor: (facts: object) => { text: string; subject: string; email: string; issue: string | null };
    safeRoute: (route: unknown) => string;
    failureLine: (failure: object | null | undefined) => string | null;
    /** Open the Report a problem sheet; with a failure it names the method, route, status and time. */
    open: (options?: { failure?: { method?: string; route?: string; status?: number; at?: Date | string | number } | null }) => Promise<void>;
  };
}

// What words.js (the Words section of Questions) uses from app.js and review.js, which are plain
// JavaScript loaded before it. Declared loosely where the older scripts take loose values. (`api`,
// `state` and `VIEWS` are declared once, above, for every script that reads them.)

/** The right half of the page (app.js). */
declare const detailPane: HTMLElement;

/** The layout sets of app.js: the views with a wide list, an own right half, no right half, and the
 * views that still show their own with the census-only card above. */
declare const REVIEW_VIEWS: Set<string>;
declare const OWN_DETAIL: Set<string>;
declare const STAYS_WITH_CENSUS: Set<string>;

/** Other names an address answers to (app.js). */
declare const VIEW_ALIASES: { [name: string]: string };

/** Run something when a person leaves a page, and when the header has read what waits again (app.js). */
declare function onLeave(hook: (next: string) => void): void;
declare function onRefresh(hook: () => void): void;

/** The auto-advance of the pages of questions (review.js). */
declare const advance: {
  next(rows: Array<{ target: string; in_force: boolean }>, answered: string, shownBefore: string[]):
    { target: string; in_force: boolean } | null;
  focus(pane: Element): void;
  done(pane: Element, said: string, flash?: string): void;
};

/** The define row, the word's evidence and the computer readings of a word question (review.js). */
declare function defineRow(themeKinds: string[] | undefined,
  onDefine: (meaning: string, axis: string, where: string) => void,
  choices?: Array<{ value: string; label: string }>): HTMLElement;
declare function wordEvidence(shown: any): Array<Node | null>;
declare function computerChoices(shown: any, onReading: (reading: string) => void): Array<Node | null>;

/** The note box without a label (review.js). */
declare function noteInput(): HTMLInputElement;

/** A lead sentence, and the rest behind "What is this?" (review.js). */
declare function explained(lead: string, more?: string): Array<Node | null>;

/** A row the keyboard reaches: Enter opens it, the arrows move beside it (review.js). */
declare function keyedRow(node: HTMLElement, open?: (() => void) | null): HTMLElement;

/** A table of headers (a "#" first marks a number column) and rows (app.js). */
declare function table(headers: Array<string | Node>, rows: Node[]): HTMLElement;
