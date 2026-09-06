(function () {
    "use strict";

    var el = function (id) { return document.getElementById(id); };
    var domain = window.location.hostname;

    function esc(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function say(text, bad) {
        var m = el("msg");
        m.hidden = !text;
        m.textContent = text || "";
        m.className = bad ? "error" : "notice";
    }

    // ---- listing -----------------------------------------------------------
    function refresh() {
        cockpit.spawn(["/usr/local/bin/fleet-publish", "list"], { err: "message" })
            .then(function (out) {
                var lines = out.trim().split("\n").slice(1)   // drop header
                              .filter(function (l) { return l.trim() && l.indexOf("(nothing") !== 0; });
                if (!lines.length) {
                    el("rows").innerHTML = '<div class="empty">Nothing published yet.</div>';
                    return;
                }
                el("rows").innerHTML = lines.map(function (l) {
                    var p = l.trim().split(/\s+/);
                    var name = p[0], target = p[1], url = p[2];
                    return '<div class="prow">' +
                        '<div class="pname">' + esc(name) + '</div>' +
                        '<div class="ptarget">' + esc(target) + '</div>' +
                        '<a class="purl" href="' + esc(url) + '" target="_blank" rel="noopener">' +
                            esc(url) + '</a>' +
                        '<button class="btn danger" data-rm="' + esc(name) + '">Remove</button>' +
                        '</div>';
                }).join("");
                Array.prototype.forEach.call(
                    el("rows").querySelectorAll("[data-rm]"), function (b) {
                        b.addEventListener("click", function () { remove(b.getAttribute("data-rm")); });
                    });
            })
            .catch(function (e) { say("Could not list: " + (e.message || e), true); });
    }

    // ---- ports that are actually listening ---------------------------------
    function loadPorts() {
        cockpit.spawn(["/usr/local/bin/fleet-publish", "ports"], { err: "ignore" })
            .then(function (out) {
                var ports = out.trim().split("\n").filter(Boolean);
                el("portlist").innerHTML = ports.map(function (p) {
                    return '<option value="' + esc(p) + '">';
                }).join("");
                el("porthint").textContent = ports.length
                    ? "listening now: " + ports.join(", ")
                    : "no extra ports listening";
            })
            .catch(function () { /* hint only, never fatal */ });
    }

    function preview() {
        var n = el("name").value.trim(), p = el("port").value.trim();
        el("preview").innerHTML = (n && p)
            ? 'https://' + esc(domain) + '/' + esc(n) + '/ &nbsp;&rarr;&nbsp; 127.0.0.1:' + esc(p)
            : "";
    }

    // ---- actions -----------------------------------------------------------
    function add() {
        var n = el("name").value.trim(), p = el("port").value.trim();
        if (!n || !p) { say("Give both a path name and a port.", true); return; }
        say("");
        el("add").disabled = true;
        // needs root: it writes into /etc/caddy and reloads the service
        cockpit.spawn(["/usr/local/bin/fleet-publish", "add", n, p],
                      { superuser: "require", err: "message" })
            .then(function (out) {
                say(out.trim(), false);
                el("name").value = ""; el("port").value = ""; preview();
                refresh();
            })
            .catch(function (e) { say(e.message || String(e), true); })
            .finally(function () { el("add").disabled = false; });
    }

    function remove(name) {
        say("");
        cockpit.spawn(["/usr/local/bin/fleet-publish", "rm", name],
                      { superuser: "require", err: "message" })
            .then(function (out) { say(out.trim(), false); refresh(); })
            .catch(function (e) { say(e.message || String(e), true); });
    }

    el("add").addEventListener("click", add);
    el("name").addEventListener("input", preview);
    el("port").addEventListener("input", preview);
    el("port").addEventListener("focus", loadPorts);

    refresh();
    loadPorts();
})();