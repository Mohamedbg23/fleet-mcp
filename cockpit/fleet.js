(function () {
    "use strict";

    var REFRESH_MS = 10000;
    var timer = null;
    var busy = false;

    var el = function (id) { return document.getElementById(id); };

    function cls(pct) { return pct >= 90 ? "bad" : pct >= 75 ? "warn" : ""; }

    function meter(label, pct) {
        var span = '<span class="' + cls(pct) + '" style="width:' +
                   Math.max(0, Math.min(100, pct)) + '%"></span>';
        return '<div class="row"><div class="k">' + label + '</div>' +
               '<div class="bar">' + span + '</div>' +
               '<div class="v">' + pct + '%</div></div>';
    }

    function chip(name, state) {
        var good = state === "active";
        return '<span class="chip' + (good ? '' : ' bad') + '">' +
               name + ': ' + (good ? 'on' : state) + '</span>';
    }

    function esc(s) {
        return String(s === undefined || s === null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function render(rows) {
        var up = 0, cpuSum = 0, memSum = 0, warn = 0;

        var html = rows.map(function (h) {
            if (!h.online) {
                return '<div class="card off"><h2><span class="dot bad"></span>' +
                       esc(h.id) + '</h2>' +
                       '<div class="meta">unreachable &mdash; SSH did not answer</div></div>';
            }
            up++; cpuSum += h.cpu; memSum += h.mem;
            if (h.keepalive !== "active" || h.fail2ban !== "active" || h.disk >= 85) warn++;

            return '<div class="card">' +
                '<h2><span class="dot"></span>' + esc(h.host) + '</h2>' +
                '<div class="meta">up ' + esc(h.uptime) + ' &middot; load ' +
                    esc(h.load) + ' &middot; ' + esc(h.kernel) + '</div>' +
                meter("CPU", h.cpu) + meter("RAM", h.mem) + meter("Disk", h.disk) +
                '<div class="svcs">' + chip("keep-alive", h.keepalive) +
                    chip("fail2ban", h.fail2ban) + '</div>' +
                '</div>';
        }).join("");

        el("cards").innerHTML = html;

        var n = rows.length;
        var avgC = up ? Math.round(cpuSum / up) : 0;
        var avgM = up ? Math.round(memSum / up) : 0;
        // Oracle reclaims an Always Free VM only if CPU, network AND memory are
        // all under 20% for 7 days -- so surface that line explicitly.
        var safe = rows.filter(function (h) { return h.online && h.cpu >= 20; }).length;

        el("summary").innerHTML =
            stat(up + " / " + n, "servers up") +
            stat(avgC + "%", "avg cpu") +
            stat(avgM + "%", "avg ram") +
            stat(safe + " / " + up, "above idle limit") +
            stat(warn === 0 ? "OK" : String(warn), warn === 0 ? "all healthy" : "need attention");
    }

    function stat(n, l) {
        return '<div class="stat"><div class="n">' + esc(n) + '</div>' +
               '<div class="l">' + esc(l) + '</div></div>';
    }

    function load() {
        if (busy) return;
        busy = true;
        el("refresh").disabled = true;

        cockpit.spawn(["/usr/local/bin/fleet-status-json"], { err: "message" })
            .then(function (out) {
                var rows;
                try { rows = JSON.parse(out); }
                catch (e) { throw new Error("bad output from fleet-status-json:\n" + out); }
                el("error").hidden = true;
                render(rows);
                el("updated").textContent =
                    "updated " + new Date().toLocaleTimeString();
            })
            .catch(function (e) {
                el("error").hidden = false;
                el("error").textContent =
                    "Could not read fleet status.\n" + (e.message || e) +
                    "\n\nCheck: /usr/local/bin/fleet-status-json exists and is executable, " +
                    "and this user can ssh to the other hosts with ~/.ssh/fleet.";
            })
            .finally(function () {
                busy = false;
                el("refresh").disabled = false;
            });
    }

    el("refresh").addEventListener("click", load);

    // Poll only while the tab is actually visible: a background tab, or a
    // closed panel, costs nothing at all.
    function schedule() {
        clearInterval(timer);
        if (!document.hidden) {
            load();
            timer = setInterval(load, REFRESH_MS);
        }
    }
    document.addEventListener("visibilitychange", schedule);
    schedule();
})();