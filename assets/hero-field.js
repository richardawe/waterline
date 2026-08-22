/* Subtle animated dot-field for the hero background — hand-rolled canvas,
   no dependencies. A drifting grid of points, faintly connected to their
   nearest neighbors, that brighten near the cursor. Restrained enough to sit
   behind text but visible at rest, not just on hover. Respects
   prefers-reduced-motion by rendering one static frame instead of animating. */
(function () {
  const canvas = document.getElementById('hero-field');
  if (!canvas || !canvas.getContext) return;
  const ctx = canvas.getContext('2d');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  let dpr = Math.min(window.devicePixelRatio || 1, 2);
  let width = 0, height = 0, points = [];
  const SPACING = 58;
  const LINK_DIST = SPACING * 1.5;
  const mouse = { x: -9999, y: -9999 };

  function buildPoints() {
    points = [];
    const cols = Math.ceil(width / SPACING) + 1;
    const rows = Math.ceil(height / SPACING) + 1;
    for (let i = 0; i < cols; i++) {
      for (let j = 0; j < rows; j++) {
        points.push({
          baseX: i * SPACING,
          baseY: j * SPACING,
          phase: Math.random() * Math.PI * 2,
        });
      }
    }
  }

  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect();
    width = rect.width;
    height = rect.height;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    buildPoints();
  }

  function draw(t) {
    ctx.clearRect(0, 0, width, height);
    const time = t / 1000;
    const resolved = points.map((p) => {
      const drift = reduceMotion ? 0 : Math.sin(time * 0.35 + p.phase) * 4;
      const x = p.baseX + drift;
      const y = p.baseY + (reduceMotion ? 0 : Math.cos(time * 0.28 + p.phase) * 4);
      const dx = x - mouse.x, dy = y - mouse.y;
      const proximity = Math.max(0, 1 - Math.sqrt(dx * dx + dy * dy) / 240);
      return { x, y, phase: p.phase, proximity };
    });

    // faint links between near neighbors — grid spacing keeps this cheap (no O(n^2) scan needed)
    ctx.lineWidth = 1;
    for (let i = 0; i < resolved.length; i++) {
      const a = resolved[i];
      for (let j = i + 1; j < resolved.length; j++) {
        const b = resolved[j];
        const dx = a.x - b.x, dy = a.y - b.y;
        const d2 = dx * dx + dy * dy;
        if (d2 > LINK_DIST * LINK_DIST) continue;
        const linkAlpha = (1 - Math.sqrt(d2) / LINK_DIST) * (0.05 + Math.max(a.proximity, b.proximity) * 0.35);
        if (linkAlpha < 0.008) continue;
        ctx.strokeStyle = `rgba(226,163,91,${linkAlpha})`;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }

    for (const p of resolved) {
      const baseAlpha = 0.16 + Math.sin(time * 0.6 + p.phase) * 0.08;
      const alpha = Math.min(0.85, baseAlpha + p.proximity * 0.5);
      const radius = 1.3 + p.proximity * 1.6;
      ctx.beginPath();
      ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(226,163,91,${alpha})`;
      ctx.fill();
    }

    if (!reduceMotion) requestAnimationFrame(draw);
  }

  window.addEventListener('resize', resize, { passive: true });
  canvas.parentElement.addEventListener('mousemove', (e) => {
    const rect = canvas.getBoundingClientRect();
    mouse.x = e.clientX - rect.left;
    mouse.y = e.clientY - rect.top;
  }, { passive: true });
  canvas.parentElement.addEventListener('mouseleave', () => {
    mouse.x = -9999;
    mouse.y = -9999;
  });

  resize();
  if (reduceMotion) {
    draw(0);
  } else {
    requestAnimationFrame(draw);
  }
})();
