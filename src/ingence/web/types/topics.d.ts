// The topic types (topic_kit.js, topics.js), and what they put on the window for the views.
//
// Written by hand beside globals.d.ts and the generated api.d.ts. Every global of app.js and
// review.js these scripts read (api, navigate, state, VIEWS, detailPane, table, onRefresh ...) is
// declared once, in globals.d.ts, never here.

/** A topic as the views hold it. */
interface TopicKitTopic {
  id: string;
  /** The Record target it is written on (``overlay:group:<id>``). */
  target: string;
  name: string;
  /** Its place in the order the topics were made: it decides its color. */
  index: number;
  /** The color of its marks, from the magenta family (a person's), as a CSS value. */
  color: string;
  /** The experiments it holds in the run, by identifier. */
  experiments: Set<string>;
  /** How many experiments it holds in the run; null where the run applied no rules (unknown, never 0). */
  count: number | null;
  /** False where a member could not be read: the experiments are then those that could, at least. */
  complete: boolean;
}

/** Every topic read against the run, held for the views. */
interface TopicKitReading {
  run: string;
  topics: TopicKitTopic[];
  /** For each experiment, the topics that hold it. */
  byExperiment: Map<string, TopicKitTopic[]>;
  /** Every experiment of the run; null where the run applied no rules (unknown, never 0). */
  runExperiments: number | null;
}

/** One thing to add to a topic, in the words the overlay route reads. */
type TopicMemberSpec =
  | { kind: 'experiment'; id: string }
  | { kind: 'entry'; id: string }
  | { kind: 'core'; terms: string[] }
  | { kind: 'line'; experiments: string[]; words: string[]; label?: string };

/** What the chooser gives back: the topic the things went into, and how many were new to it. */
interface TopicAdded {
  id: string;
  name: string;
  added: number;
}

/** The pieces every view shares for topics (topic_kit.js). */
interface TopicKit {
  /** Every topic and the experiments it holds, read once and kept until a topic changes. */
  read(options?: { force?: boolean }): Promise<TopicKitReading>;
  /** What was last read, or null before the first reading. */
  current(): TopicKitReading | null;
  /** Called when a topic is made, added to, renamed or withdrawn; returns the way to stop. */
  subscribe(listener: () => void): () => void;
  /** Tell every view that a topic changed (after a write that was not the chooser's). */
  changed(): void;
  colorOf(topic: { index: number }): string;
  /** The chooser: a new topic by name, or one of the existing; writes through the overlay route. */
  addTo(options: { members: TopicMemberSpec[]; what: string }): Promise<TopicAdded | null>;
  /** The bar a view shows while things are picked. */
  pickBar(options: { noun: string; count: number; onAdd: () => void; onClear: () => void }): HTMLElement;
  /** "Also in" chips for the topics that hold any of these experiments, or null for none; ``label``
   * is the words before the chips ("also in " by default). */
  chipsFor(experiments: string[], options?: { limit?: number; label?: string }): HTMLElement | null;
  /** A line of work's key as the server makes it, from the experiments it was added by (null where the browser has no digest). */
  lineKey(experiments: string[]): Promise<string | null>;
  /** Open the Topics section on one topic. */
  open(topicId: string): void;
  /** Say something short and quiet at the foot of the page. */
  say(text: string, options?: { topicId?: string }): void;
}

interface Window {
  topicKit: TopicKit;
  /** The Topics section's entry points, for the chips and the views that open it. */
  ingenceTopics?: { show(topicId?: string): Promise<unknown> };
}

declare const topicKit: TopicKit;
