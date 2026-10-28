// The Lineage layout's worker: dagre runs here, so the page keeps answering while it lays out.

import {computeLayout, type LayoutRequest} from './layoutCore';

self.onmessage = (event: MessageEvent<{serial: number; request: LayoutRequest}>) => {
  const result = computeLayout(event.data.request);
  (self as unknown as Worker).postMessage({serial: event.data.serial, result});
};
