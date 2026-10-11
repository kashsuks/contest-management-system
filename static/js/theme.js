// Applies the saved (or system) colour theme before first paint and wires up any theme toggle buttons.
(function () {
    var root = document.documentElement;
    var stored = null;
    try { stored = localStorage.getItem('theme'); } catch (e) { /* storage unavailable */ }
    var theme = stored || (window.matchMedia && matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    root.setAttribute('data-theme', theme);

    window.toggleTheme = function () {
        var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        root.setAttribute('data-theme', next);
        try { localStorage.setItem('theme', next); } catch (e) { /* ignore */ }
    };

    document.addEventListener('click', function (event) {
        if (event.target.closest('[data-theme-toggle]')) {
            window.toggleTheme();
        }
    });
})();
