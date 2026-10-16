(function () {
    function init() {
        var input = document.getElementById("vdSearch");
        var grid = document.getElementById("vdGrid");
        if (!input || !grid) return;
        input.addEventListener("input", function () {
            var q = (input.value || "").toLowerCase().trim();
            var tiles = grid.querySelectorAll(".vd-tile");
            tiles.forEach(function (t) {
                var name = (t.getAttribute("data-name") || "");
                if (q && name.indexOf(q) === -1) {
                    t.classList.add("vd-hidden");
                } else {
                    t.classList.remove("vd-hidden");
                }
            });
        });
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
