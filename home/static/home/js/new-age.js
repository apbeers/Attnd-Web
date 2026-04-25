(function () {
  "use strict";

  // Close navbar collapse when any nav link is clicked (mobile UX)
  document.querySelectorAll('#navbarResponsive .nav-link').forEach(function (link) {
    link.addEventListener('click', function () {
      var navbarCollapse = document.getElementById('navbarResponsive');
      if (navbarCollapse && navbarCollapse.classList.contains('show')) {
        var bsCollapse = bootstrap.Collapse.getInstance(navbarCollapse);
        if (bsCollapse) bsCollapse.hide();
      }
    });
  });

  // Shrink navbar on scroll
  function navbarShrink() {
    var mainNav = document.getElementById('mainNav');
    if (!mainNav) return;
    if (window.scrollY > 100) {
      mainNav.classList.add('navbar-shrink');
    } else {
      mainNav.classList.remove('navbar-shrink');
    }
  }
  navbarShrink();
  window.addEventListener('scroll', navbarShrink, { passive: true });

})();
