// The Graph workspace's entry: the Dashboard's app.js calls window.ingenceExplorer.mount when the
// Explorer section opens, and unmount when a person leaves it. Nothing here runs until then.

import {StrictMode} from 'react';
import {createRoot, type Root} from 'react-dom/client';

import './theme.css';
import {Workspace} from './Workspace';

declare global {
  interface Window {
    ingenceExplorer?: {mount(element: HTMLElement): void; unmount(): void};
  }
}

let root: Root | null = null;

function unmount(): void {
  root?.unmount();
  root = null;
}

function mount(element: HTMLElement): void {
  unmount();
  const host = document.createElement('div');
  host.className = 'ix-host';
  element.replaceChildren(host);
  root = createRoot(host);
  root.render(
    <StrictMode>
      <Workspace />
    </StrictMode>,
  );
}

window.ingenceExplorer = {mount, unmount};
