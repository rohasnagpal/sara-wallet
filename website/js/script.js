// ═══════════════════════════════════════════════════════════════════════
// SARA — marketing one-pager interactions
// Nav scroll state, mobile menu, scroll-reveal, and the hero chat demo.
// No frameworks — this is a static one-pager, plain DOM APIs are enough.
// ═══════════════════════════════════════════════════════════════════════

(function () {
  'use strict';

  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ── Footer year ─────────────────────────────────────────────────────
  const yearEl = document.getElementById('year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  // ── Nav: scrolled state + mobile toggle ────────────────────────────
  const nav = document.getElementById('nav');
  const navToggle = document.getElementById('navToggle');

  function updateNavScrollState() {
    if (!nav) return;
    nav.classList.toggle('is-scrolled', window.scrollY > 12);
  }
  updateNavScrollState();
  window.addEventListener('scroll', updateNavScrollState, { passive: true });

  if (navToggle && nav) {
    navToggle.addEventListener('click', () => {
      const isOpen = nav.classList.toggle('is-menu-open');
      navToggle.classList.toggle('is-open', isOpen);
      navToggle.setAttribute('aria-expanded', String(isOpen));
    });

    // Close the mobile menu after tapping a link, so navigating actually
    // shows the destination section instead of the menu staying pinned open.
    nav.querySelectorAll('.nav-links a, .nav-cta a').forEach((link) => {
      link.addEventListener('click', () => {
        nav.classList.remove('is-menu-open');
        navToggle.classList.remove('is-open');
        navToggle.setAttribute('aria-expanded', 'false');
      });
    });
  }

  // ── Scroll reveal ────────────────────────────────────────────────────
  // Elements marked [data-reveal] fade/slide in once they enter the
  // viewport. Reduced-motion users get everything visible immediately
  // instead of waiting on an observer that mostly just delays content.
  const revealTargets = document.querySelectorAll('[data-reveal]');
  if (prefersReducedMotion || !('IntersectionObserver' in window)) {
    revealTargets.forEach((el) => el.classList.add('is-visible'));
  } else {
    const revealObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible');
            revealObserver.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.14, rootMargin: '0px 0px -40px 0px' }
    );
    revealTargets.forEach((el) => revealObserver.observe(el));
  }

  // ── Hero chat demo: reveal bubbles one at a time, then loop ─────────
  // Purely decorative — shows the "send 50 USDT to maria" exchange
  // typing itself out so the hero communicates the product in motion,
  // not just in a static screenshot.
  const thread = document.getElementById('demoThread');
  if (thread) {
    const lines = Array.from(thread.querySelectorAll('[data-line]'));
    const caret = document.getElementById('typeCaret');
    const caretDefaultText = 'Type or say a command…';
    let cycleTimer = null;

    function resetLines() {
      lines.forEach((el) => el.classList.remove('is-in'));
    }

    function runCycle() {
      resetLines();
      if (caret) caret.textContent = caretDefaultText;

      if (prefersReducedMotion) {
        lines.forEach((el) => el.classList.add('is-in'));
        return;
      }

      const stepDelay = 900;
      lines.forEach((el, i) => {
        setTimeout(() => el.classList.add('is-in'), 500 + i * stepDelay);
      });

      const pauseAfterFinish = 3400;
      const totalRunTime = 500 + lines.length * stepDelay + pauseAfterFinish;
      cycleTimer = setTimeout(runCycle, totalRunTime);
    }

    // Only run the animated loop once the hero card is actually on screen,
    // so it's not silently burning timers/layout while scrolled past.
    if ('IntersectionObserver' in window) {
      const heroObserver = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              runCycle();
            } else if (cycleTimer) {
              clearTimeout(cycleTimer);
            }
          });
        },
        { threshold: 0.3 }
      );
      heroObserver.observe(thread);
    } else {
      runCycle();
    }
  }
})();
