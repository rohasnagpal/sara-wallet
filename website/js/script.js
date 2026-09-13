// ============================================================
// Sara Wallet — Marketing Site interactions
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
  initNav();
  initReveal();
  initCounters();
  initBars();
  initPhoneChats();
  initFaq();
});

// ---------- Nav scroll state + mobile toggle ----------

function initNav() {
  const nav = document.querySelector('.nav');
  const toggle = document.querySelector('.nav-toggle');
  const links = document.querySelector('.nav-links');

  if (nav) {
    const onScroll = () => nav.classList.toggle('scrolled', window.scrollY > 20);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  if (toggle && links) {
    toggle.addEventListener('click', () => {
      const open = links.style.display === 'flex';
      links.style.display = open ? 'none' : 'flex';
      links.style.flexDirection = 'column';
      links.style.position = 'absolute';
      links.style.top = '100%';
      links.style.left = '0';
      links.style.right = '0';
      links.style.background = 'rgba(246,244,236,0.98)';
      links.style.padding = '24px 28px';
      links.style.gap = '18px';
      links.style.borderBottom = '1px solid rgba(20,20,15,0.1)';
    });
  }
}

// ---------- Scroll reveal ----------

function initReveal() {
  const els = document.querySelectorAll('.reveal');
  if (!els.length) return;

  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('in');
          io.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.15, rootMargin: '0px 0px -60px 0px' }
  );

  els.forEach((el, i) => {
    el.style.transitionDelay = `${(i % 4) * 70}ms`;
    io.observe(el);
  });
}

// ---------- Animated stat counters ----------

function initCounters() {
  const els = document.querySelectorAll('[data-count]');
  if (!els.length) return;

  const animate = (el) => {
    const target = parseFloat(el.dataset.count);
    const decimals = el.dataset.decimals ? parseInt(el.dataset.decimals, 10) : 0;
    const duration = 1600;
    const start = performance.now();

    const tick = (now) => {
      const p = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      const value = target * eased;
      el.textContent = decimals ? value.toFixed(decimals) : Math.round(value).toLocaleString();
      if (p < 1) requestAnimationFrame(tick);
      else el.textContent = decimals ? target.toFixed(decimals) : target.toLocaleString();
    };
    requestAnimationFrame(tick);
  };

  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          animate(entry.target);
          io.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.5 }
  );

  els.forEach((el) => io.observe(el));
}

// ---------- Comparison bar fills ----------

function initBars() {
  const bars = document.querySelectorAll('.bar-fill');
  if (!bars.length) return;

  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.style.width = entry.target.dataset.width;
          io.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.4 }
  );

  bars.forEach((bar) => io.observe(bar));
}

// ---------- Phone mockup chat sequencing ----------

function initPhoneChats() {
  const phones = document.querySelectorAll('.phone-chat');
  if (!phones.length) return;

  phones.forEach((chat) => {
    const bubbles = Array.from(chat.querySelectorAll('.p-bubble'));

    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            bubbles.forEach((b, i) => {
              setTimeout(() => {
                b.style.opacity = '1';
                b.style.transform = 'translateY(0)';
              }, i * 550);
            });
            io.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.4 }
    );

    io.observe(chat);
  });
}

// ---------- FAQ accordion ----------

function initFaq() {
  const items = document.querySelectorAll('.faq-item');
  if (!items.length) return;

  items.forEach((item) => {
    const q = item.querySelector('.faq-q');
    const toggle = item.querySelector('.faq-toggle');

    q.addEventListener('click', () => {
      const isOpen = item.classList.contains('open');

      items.forEach((other) => {
        other.classList.remove('open');
        other.querySelector('.faq-toggle').textContent = '+';
      });

      if (!isOpen) {
        item.classList.add('open');
        toggle.textContent = '−';
      }
    });
  });
}
