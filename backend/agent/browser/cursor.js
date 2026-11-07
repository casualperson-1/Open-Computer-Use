(function() {
  if (window.__agent_cursor_installed__) return;
  window.__agent_cursor_installed__ = true;

  const cursor = document.createElement('div');
  cursor.id = '__agent_mouse_cursor__';
  cursor.style.position = 'fixed';
  cursor.style.top = '0px';
  cursor.style.left = '0px';
  cursor.style.width = '24px';
  cursor.style.height = '24px';
  cursor.style.pointerEvents = 'none';
  cursor.style.zIndex = '2147483647';
  cursor.style.transition = 'transform 0.04s linear';
  cursor.style.transform = 'translate(-100px, -100px)';

  // Red/white high-visibility SVG cursor
  cursor.innerHTML = `
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M5.5 3.21V20.8c0 .45.54.67.85.35l4.86-4.86a.5.5 0 0 1 .35-.15h6.87c.45 0 .67-.54.35-.85L6.35 2.86a.5.5 0 0 0-.85.35z" 
            fill="#FF2E54" stroke="#FFFFFF" stroke-width="1.8" stroke-linejoin="round"/>
    </svg>
  `;

  const rippleContainer = document.createElement('div');
  rippleContainer.id = '__agent_mouse_ripples__';
  rippleContainer.style.position = 'fixed';
  rippleContainer.style.top = '0px';
  rippleContainer.style.left = '0px';
  rippleContainer.style.width = '100vw';
  rippleContainer.style.height = '100vh';
  rippleContainer.style.pointerEvents = 'none';
  rippleContainer.style.zIndex = '2147483646';

  function attach() {
    if (document.body && !document.getElementById('__agent_mouse_cursor__')) {
      document.body.appendChild(rippleContainer);
      document.body.appendChild(cursor);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attach);
  } else {
    attach();
  }

  window.addEventListener('mousemove', (e) => {
    attach();
    cursor.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
  }, true);

  window.addEventListener('mousedown', (e) => {
    attach();
    createRipple(e.clientX, e.clientY);
  }, true);

  function createRipple(x, y) {
    const ripple = document.createElement('div');
    ripple.style.position = 'absolute';
    ripple.style.left = `${x - 12}px`;
    ripple.style.top = `${y - 12}px`;
    ripple.style.width = '24px';
    ripple.style.height = '24px';
    ripple.style.borderRadius = '50%';
    ripple.style.border = '3px solid #FF2E54';
    ripple.style.backgroundColor = 'rgba(255, 46, 84, 0.3)';
    ripple.style.pointerEvents = 'none';
    ripple.style.transform = 'scale(0.5)';
    ripple.style.opacity = '1';
    ripple.style.transition = 'transform 0.4s ease-out, opacity 0.4s ease-out';

    rippleContainer.appendChild(ripple);

    requestAnimationFrame(() => {
      ripple.style.transform = 'scale(2.2)';
      ripple.style.opacity = '0';
    });

    setTimeout(() => {
      if (ripple.parentNode) {
        ripple.parentNode.removeChild(ripple);
      }
    }, 450);
  }
})();
