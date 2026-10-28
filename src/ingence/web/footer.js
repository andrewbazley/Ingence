// @ts-check
/* The footer's version: ``ingence 0.3.1 · schema 1.23`` in mono. It is read from the engine
 * (``/api/v1/version``) once the page has loaded and the browser is idle. The footer's other two
 * parts are plain links in index.html: the documentation, and "Report a problem" (report.js, which
 * opens the sheet). The footer holds no build hash. The hash is in the text of a problem report. If
 * the version cannot be read, the footer says "ingence" and nothing else. The read is shared:
 * ``window.ingenceVersion()`` gives report.js the same answer. This file loads first and stands
 * alone, so the footer is right whatever else fails to load. */

(() => {
  /** @type {Promise<any> | null} */
  let versionRead = null;

  /** The engine's version answer, read once for the page; null if it cannot be read. report.js
   * uses the same read.
   * @returns {Promise<any>} */
  function readVersion() {
    if (!versionRead) {
      versionRead = fetch('/api/v1/version')
        .then((response) => (response.ok ? response.json() : null))
        .catch(() => null);
    }
    return versionRead;
  }

  /** Show the version and the schema in the footer. Never throws. */
  async function showVersion() {
    const slot = document.getElementById('footer-version');
    if (!slot) return;
    const version = await readVersion();
    if (!version || typeof version.engine_version !== 'string') return;
    const schema = typeof version.schema_version === 'string' ? ` \u00b7 schema ${version.schema_version}` : '';
    slot.textContent = `ingence ${version.engine_version}${schema}`;
  }

  window.ingenceVersion = readVersion;

  // After the page has loaded and the browser is idle, so the footer adds nothing while the page
  // is being drawn.
  window.addEventListener('load', () => {
    if (typeof window.requestIdleCallback === 'function') {
      window.requestIdleCallback(() => { showVersion(); }, {timeout: 3000});
    } else {
      window.setTimeout(() => { showVersion(); }, 1000);
    }
  });
})();
