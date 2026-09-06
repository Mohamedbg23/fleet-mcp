(function () {
    "use strict";

    var el = function (id) { return document.getElementById(id); };
    var domain = window.location.hostname;
    var mode = "path";          // "path" | "domain"
    var serverIp = null;

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

    // ---- mode switch -------------------------------------------------------
    function setMode(m) {
        mode = m;
        el("m-path").classList.toggle("active", m === "path");
        el("m-dom").classList.toggle("active", m === "domain");
        el("f-name").hidden = (m === "domain");
        el("f-dom").hidden = (m !== "domain");
        el("dnshelp").hidden = (m !== "domain");
        if (m === "domain") showDnsHelp();
        say("");
        preview();
    }

    function showDnsHelp() {
        el("dnshelp").innerHTML =
            '<strong>Before this works:</strong> at your registrar, point the domain at this server.' +
            '<div class="dnsrec"><span>Type</span><span>Name</span><span>Value</span></div>' +
            '<div class="dnsrec mono"><span>A</span><span>@</span><span>' +
                esc(serverIp || "loading…") + '</span></div>' +
            '<p class="tip">Better: use Cloudflare DNS and set <b>CNAME @ &rarr; ' + esc(domain) +
            '</b> instead. Then it follows this server automatically if the IP ever changes.</p>';
    }

    // ---- listing -----------------------------------------------------------
    function refresh() {
        cockpit.spawn(["/usr/local/bin/fleet-publish", "list"], { err: "message" })
            .then(function (out) {
                var lines = out.trim().split("\n").slice(1)
                              .filter(function (l) { return l.trim() && l.indexOf("(nothing") !== 0; });
                if (!lines.length) {
                    el("rows").innerHTML = '<div class="empty">Nothing published yet.</div>';
                    return;
                }
                el("rows").innerHTML = lines.map(function (l) {
                    var p = l.trim().split(/\s+/);
                    var name = p[0], target = p[1], url = p[2] || "";
                    var own = url.indexOf("//" + domain + "/") === -1;
                    return '<div class="prow">' +
                        '<div class="pname">' + esc(name) +
                            (own ? ' <span class="tag">domain</span>' : ' <span class="tag alt">path</span>') +
                        '</div>' +
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
            .catch(function () {});
    }

    function loadIp() {
        cockpit.spawn(["bash", "-c", "curl -s --max-time 8 https://api.ipify.org"], { err: "ignore" })
            .then(function (out) {
                serverIp = out.trim();
                if (mode === "domain") showDnsHelp();
            })
            .catch(function () {});
    }

    function preview() {
        var p = el("port").value.trim();
        var t = "";
        if (mode === "domain") {
            var d = el("domain").value.trim();
            if (d && p) t = "https://" + esc(d) + "/ &nbsp;&rarr;&nbsp; 127.0.0.1:" + esc(p);
        } else {
            var n = el("name").value.trim();
            if (n && p) t = "https://" + esc(domain) + "/" + esc(n) + "/ &nbsp;&rarr;&nbsp; 127.0.0.1:" + esc(p);
        }
        el("preview").innerHTML = t;
    }

    // ---- actions -----------------------------------------------------------
    function add() {
        var p = el("port").value.trim();
        var args;

        if (mode === "domain") {
            var d = el("domain").value.trim().toLowerCase();
            if (!d || !p) { say("Give both a domain and a port.", true); return; }
            if (!/^[a-z0-9.-]+\.[a-z]{2,}$/.test(d)) { say("That does not look like a domain.", true); return; }
            // the file is named after the domain, with dots turned into dashes
            var id = d.replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
            args = ["/usr/local/bin/fleet-publish", "add", id, p, "--domain", d];
        } else {
            var n = el("name").value.trim().toLowerCase();
            if (!n || !p) { say("Give both a path name and a port.", true); return; }
            args = ["/usr/local/bin/fleet-publish", "add", n, p];
        }

        say("");
        el("add").disabled = true;
        cockpit.spawn(args, { superuser: "require", err: "message" })
            .then(function (out) {
                say(out.trim(), out.indexOf("warning") === 0);
                el("name").value = ""; el("domain").value = ""; el("port").value = "";
                preview();
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

    el("m-path").addEventListener("click", function () { setMode("path"); });
    el("m-dom").addEventListener("click", function () { setMode("domain"); });
    el("add").addEventListener("click", add);
    ["name", "domain", "port"].forEach(function (id) {
        el(id).addEventListener("input", preview);
    });
    el("port").addEventListener("focus", loadPorts);

    refresh();
    loadPorts();
    loadIp();
})();