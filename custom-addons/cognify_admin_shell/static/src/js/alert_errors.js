(function () {
    function show(msg) {
        try {
            var d = document.createElement("div");
            d.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:999999;background:#ff3b30;color:#fff;padding:10px;font-family:monospace;font-size:12px;white-space:pre-wrap;";
            d.textContent = msg;
            if (document.body) document.body.prepend(d);
            else document.documentElement.prepend(d);
        } catch (e) {}
    }
    window.addEventListener("error", function (e) {
        show("JS ERROR: " + e.message + " @ " + e.filename + ":" + e.lineno + ":" + e.colno);
    });
    window.addEventListener("unhandledrejection", function (e) {
        show("Promise rejection: " + (e.reason && (e.reason.stack || e.reason.message || e.reason)));
    });
    setTimeout(function () {
        if (!document.querySelector(".o_view_controller") && !document.querySelector(".vuma-sidebar")) {
            show("PAGE APPEARS BLANK: no view or sidebar rendered after 8 seconds");
        }
    }, 8000);
})();
