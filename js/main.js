/**
 * MY BLOG - Main JavaScript
 * Handles: Side navigation, Dark/Light mode toggle
 */

(function() {
  'use strict';

  // ============================================
  // DOM ELEMENTS
  // ============================================
  
  const menuToggle = document.querySelector('.menu-toggle');
  const sideNav = document.querySelector('.side-nav');
  const sideNavOverlay = document.querySelector('.side-nav-overlay');
  const sideNavClose = document.querySelector('.side-nav__close');
  const themeToggle = document.querySelector('.theme-toggle');

  // ============================================
  // SIDE NAVIGATION
  // ============================================

  /**
   * Open the side navigation
   */
  function openSideNav() {
    sideNav.classList.add('active');
    sideNavOverlay.classList.add('active');
    menuToggle.classList.add('active');
    document.body.style.overflow = 'hidden';
  }

  /**
   * Close the side navigation
   */
  function closeSideNav() {
    sideNav.classList.remove('active');
    sideNavOverlay.classList.remove('active');
    menuToggle.classList.remove('active');
    document.body.style.overflow = '';
  }

  /**
   * Toggle the side navigation
   */
  function toggleSideNav() {
    if (sideNav.classList.contains('active')) {
      closeSideNav();
    } else {
      openSideNav();
    }
  }

  // Event Listeners for Side Navigation
  if (menuToggle) {
    menuToggle.addEventListener('click', toggleSideNav);
  }

  if (sideNavClose) {
    sideNavClose.addEventListener('click', closeSideNav);
  }

  if (sideNavOverlay) {
    sideNavOverlay.addEventListener('click', closeSideNav);
  }

  // Close side nav on escape key
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && sideNav && sideNav.classList.contains('active')) {
      closeSideNav();
    }
  });

  // ============================================
  // DARK MODE / LIGHT MODE TOGGLE
  // ============================================

  // Theme storage key
  const THEME_STORAGE_KEY = 'blog-theme-preference';

  /**
   * Get the user's theme preference
   * Priority: localStorage > system preference > default (light)
   */
  function getThemePreference() {
    // Check localStorage first
    const storedTheme = localStorage.getItem(THEME_STORAGE_KEY);
    if (storedTheme) {
      return storedTheme;
    }

    // Check system preference
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      return 'dark';
    }

    // Default to light
    return 'light';
  }

  /**
   * Apply the theme to the document
   */
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  }

  /**
   * Toggle between light and dark themes
   */
  function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';
    applyTheme(newTheme);
  }

  // Initialize theme on page load
  function initTheme() {
    const theme = getThemePreference();
    applyTheme(theme);
  }

  // Apply theme immediately to prevent flash
  initTheme();

  // Event Listener for Theme Toggle
  if (themeToggle) {
    themeToggle.addEventListener('click', toggleTheme);
  }

  // Listen for system theme changes
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
      // Only auto-switch if user hasn't manually set a preference
      if (!localStorage.getItem(THEME_STORAGE_KEY)) {
        applyTheme(e.matches ? 'dark' : 'light');
      }
    });
  }

  // ============================================
  // SMOOTH SCROLL FOR ANCHOR LINKS
  // ============================================

  document.querySelectorAll('a[href^="#"]').forEach(function(anchor) {
    anchor.addEventListener('click', function(e) {
      const targetId = this.getAttribute('href');
      if (targetId === '#') return;
      
      const targetElement = document.querySelector(targetId);
      if (targetElement) {
        e.preventDefault();
        targetElement.scrollIntoView({
          behavior: 'smooth',
          block: 'start'
        });
      }
    });
  });

  // ============================================
  // CUSDIS DARK MODE SYNC
  // ============================================

  /**
   * Update Cusdis theme to match site theme
   */
  function updateCusdisTheme() {
    const cusdisThread = document.querySelector('#cusdis_thread');
    if (cusdisThread && window.CUSDIS) {
      const currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
      window.CUSDIS.setTheme(currentTheme);
    }
  }

  // Create a mutation observer to watch for theme changes
  const observer = new MutationObserver(function(mutations) {
    mutations.forEach(function(mutation) {
      if (mutation.attributeName === 'data-theme') {
        updateCusdisTheme();
      }
    });
  });

  // Start observing theme changes
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme']
  });

})();
