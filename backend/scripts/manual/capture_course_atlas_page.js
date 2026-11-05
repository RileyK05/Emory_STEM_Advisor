// Runs in an atlas.emory.edu tab: drives the public search form and saves what
// the page renders through the local helper
// (`python -m scripts.manual.capture_course_atlas --allow-atlas-page --port 8791`).
// It only sets the visible form controls and clicks visible results, one at a
// time; it never calls the Atlas API itself. Stop with `AtlasCapture.stop = true`.
window.AtlasCapture = (() => {
  const state = {stop: false, running: false, log: [], failures: [], progress: null};
  const helper = 'http://127.0.0.1:8791/capture';
  const worker = new Worker(URL.createObjectURL(new Blob(
    ['onmessage=e=>setTimeout(()=>postMessage(e.data[0]),e.data[1])'],
    {type: 'text/javascript'})));
  const pending = new Map();
  let nextTimer = 0;
  worker.onmessage = (e) => { pending.get(e.data)(); pending.delete(e.data); };
  // A worker timer keeps its pace when Chrome throttles a hidden tab.
  const sleep = (ms) => new Promise((done) => {
    const id = ++nextTimer; pending.set(id, done); worker.postMessage([id, ms]);
  });

  async function until(test, timeout) {
    const started = performance.now();
    while (performance.now() - started < timeout) {
      const value = test();
      if (value) return value;
      await sleep(60);
    }
    return null;
  }

  async function post(records) {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const response = await fetch(helper, {method: 'POST',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify(records)});
        if (response.ok) return;
        state.log.push(`helper refused a batch: ${response.status}`);
      } catch (error) { state.log.push(String(error)); }
      await sleep(1500);
    }
    throw new Error('capture helper unreachable');
  }

  const select = (id, value) => {
    const field = document.getElementById(id);
    field.value = value;
    field.dispatchEvent(new Event('change', {bubbles: true}));
  };
  const envelope = (kind, html, term, career, extra = {}) => ({...extra, kind, html, term, career,
    source_url: location.href, observed_at: new Date().toISOString()});

  async function search(srcdb, term, careerValue, career) {
    select('crit-srcdb', srcdb);
    select('crit-career', careerValue);
    document.querySelector('.panel--kind-results .result-criteria')?.remove();
    document.getElementById('search-button').click();
    const panel = await until(() => {
      const found = document.querySelector('.panel--kind-results');
      const criteria = [...(found?.querySelectorAll('.result-criteria__item') || [])]
        .map((item) => item.textContent.trim());
      return criteria.includes(`Term: ${term}`) && criteria.includes(`Career: ${career}`)
        && !found.classList.contains('panel--busy') ? found : null;
    }, 30000);
    if (!panel) throw new Error(`no results panel for ${term} / ${career}`);
    return panel;
  }

  // Atlas closes a panel through an animation frame, which a hidden tab never
  // runs, so old panels pile up. Removing them also rules out a stale match.
  const clearDetails = () => document.querySelectorAll('.panel--kind-details')
    .forEach((panel) => panel.remove());

  function loadedDetail(link) {
    const panel = document.querySelector('.panel--kind-details');
    if (!panel || panel.classList.contains('panel--busy')) return null;
    const code = link.dataset.group.replace(/^code:/, '');
    if (panel.querySelector('.dtl-course-code')?.textContent.trim() !== code) return null;
    const sections = [...panel.querySelectorAll('a.course-section')];
    if (!sections.length || sections.some((item) => item.dataset.srcdb !== link.dataset.srcdb)) {
      return null;
    }
    const classNumber = panel.querySelector('.dtl-section')?.textContent
      .match(/Class\s+Nbr\s*:?\s*(\S+)/i)?.[1] ?? null;
    const expected = link.dataset.key ? link.dataset.key.replace(/^crn:/, '') : null;
    return classNumber === expected ? panel : null;
  }

  const identity = (link) => [link.dataset.group, link.dataset.key, link.dataset.matched,
    link.dataset.srcdb].join('|');

  // After a long run a result click can stop doing anything; Reset Search plus a
  // new search restores the page. The saved index stays the one in force.
  async function reopen(job) {
    document.querySelector('[data-action="panel-home"]')?.click();
    await sleep(1200);
    const panel = await search(job.srcdb, job.term, job.careerValue, job.career);
    state.log.push(`recovered at ${job.term} / ${job.career}`);
    return [...panel.querySelectorAll('a.result__link')];
  }

  async function details(job, firstLinks, cursors, pace) {
    const {term, career} = job;
    let links = firstLinks;
    let batch = [];
    for (const cursor of cursors) {
      if (state.stop) break;
      const expected = identity(links[cursor]);
      let link = links[cursor];
      let panel = null;
      for (let attempt = 0; attempt < 3 && !panel; attempt += 1) {
        clearDetails();
        link.click();
        panel = await until(() => loadedDetail(link), 8000);
        if (!panel && attempt < 2) {
          links = await reopen(job);
          link = links[cursor];
          if (!link || identity(link) !== expected) throw new Error(`rows moved at ${cursor}`);
        }
      }
      state.progress = {term, career, cursor, of: links.length, hidden: document.hidden};
      if (panel) {
        batch.push(envelope('detail', panel.outerHTML, term, career, {
          index_cursor: cursor,
          result_identity: {group: link.dataset.group, key: link.dataset.key,
            matched: link.dataset.matched, srcdb: link.dataset.srcdb,
            label: link.innerText},
        }));
      } else {
        state.failures.push({term, career, cursor, group: link.dataset.group,
          key: link.dataset.key});
      }
      if (batch.length >= 20) { await post(batch); batch = []; }
      await sleep(pace);
    }
    if (batch.length) await post(batch);
  }

  // jobs: [{srcdb, term, careerValue, career, index, detail, cursors}]
  // `cursors` resumes after an interruption; `index: false` keeps an earlier
  // index, because a newer one would make the details saved so far stale.
  // `detail: false` saves the index alone.
  async function run(jobs, {pace = 700} = {}) {
    if (state.running) throw new Error('already running');
    state.running = true;
    state.stop = false;
    try {
      for (const job of jobs) {
        if (state.stop) break;
        const panel = await search(job.srcdb, job.term, job.careerValue, job.career);
        const links = [...panel.querySelectorAll('a.result__link')];
        const found = panel.querySelector('.panel__info-bar-text')?.textContent.trim();
        state.log.push(`${job.term} / ${job.career}: ${found}, ${links.length} links`);
        if (job.index !== false) {
          await post([envelope('index', panel.outerHTML, job.term, job.career)]);
        }
        if (job.detail !== false) {
          const cursors = job.cursors ?? links.map((_, cursor) => cursor);
          await details(job, links, cursors, pace);
        }
        await sleep(pace);
      }
      state.log.push('finished');
    } catch (error) {
      state.log.push(`stopped: ${error}`);
    } finally {
      state.running = false;
    }
  }
  return Object.assign(state, {run});
})();
