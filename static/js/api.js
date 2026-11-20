/* =====================================================================
   MilkControl Pro — клиент API для фронтенда.
   Подключение в обеих страницах (в конце <body>):
       <script src="/static/js/api.js"></script>
   Дальше везде доступен глобальный объект MilkAPI.
   ===================================================================== */
(function (global) {
  "use strict";

  // Запасные координаты (Актобе) — если GPS недоступен или отказано в доступе
  var DEFAULT_COORDS = { lat: 50.2839, lng: 57.1670 };

  /* ---------- базовый запрос ---------- */
  async function request(url, options) {
    options = options || {};
    var init = { method: options.method || "GET", headers: {} };
    if (options.body instanceof FormData) {
      init.body = options.body;                       // multipart — заголовок выставит браузер
    } else if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    var res, data = null;
    try {
      res = await fetch(url, init);
    } catch (e) {
      throw new Error("Нет связи с сервером. Проверьте подключение.");
    }
    try { data = await res.json(); } catch (e) { /* не JSON */ }
    if (!res.ok || !data || data.ok === false) {
      throw new Error((data && data.error) || ("Ошибка сервера (" + res.status + ")"));
    }
    return data;
  }

  /* ---------- GPS ---------- */
  // Возвращает {lat, lng, accuracy, fallback}. Никогда не падает: при ошибке — запасные координаты.
  function getPosition(timeoutMs) {
    return new Promise(function (resolve) {
      if (!navigator.geolocation) {
        return resolve({ lat: DEFAULT_COORDS.lat, lng: DEFAULT_COORDS.lng, accuracy: null, fallback: true });
      }
      navigator.geolocation.getCurrentPosition(
        function (pos) {
          resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude,
                    accuracy: pos.coords.accuracy, fallback: false });
        },
        function () {
          resolve({ lat: DEFAULT_COORDS.lat, lng: DEFAULT_COORDS.lng, accuracy: null, fallback: true });
        },
        { enableHighAccuracy: true, timeout: timeoutMs || 8000, maximumAge: 10000 }
      );
    });
  }

  /* ---------- QR-сканер (нужна библиотека html5-qrcode) ----------
     Добавьте в <head> страницы агента:
       <script src="https://unpkg.com/html5-qrcode"></script>
     В QR-коде на ларе должен быть ID точки, например: POINT-007
     Возвращает объект сканера; остановить: scanner.stop() */
  function startQrScan(elementId, onScan, onError) {
    if (!global.Html5Qrcode) {
      if (onError) onError(new Error("Библиотека html5-qrcode не подключена."));
      return null;
    }
    var scanner = new global.Html5Qrcode(elementId);
    scanner.start(
      { facingMode: "environment" },
      { fps: 10, qrbox: 240 },
      function (text) {
        scanner.stop().catch(function () {});
        onScan(String(text).trim());
      },
      function () { /* пропуск кадров без QR */ }
    ).catch(function (err) { if (onError) onError(err); });
    return scanner;
  }

  /* ---------- API ---------- */
  var MilkAPI = {
    getPosition: getPosition,
    startQrScan: startQrScan,

    // Чекин визита: сам берёт GPS. Возвращает ответ сервера + использованные координаты.
    startVisit: async function (agentName, point) {
      var pos = await getPosition();
      var data = await request("/api/visit/start", {
        method: "POST",
        body: { agent_name: agentName, point: point, lat: pos.lat, lng: pos.lng }
      });
      data.position = pos;
      return data;
    },

    // status: "found" | "missing"
    verifyEquipment: function (pointId, status, agentName) {
      return request("/api/equipment/verify", {
        method: "POST",
        body: { point_id: pointId, status: status, agent_name: agentName || "" }
      });
    },

    // file — объект File из <input type="file" accept="image/*" capture="environment">
    sendPhotoReport: function (pointId, file, agentName, comment) {
      var fd = new FormData();
      fd.append("point_id", pointId);
      fd.append("photo", file);
      if (agentName) fd.append("agent_name", agentName);
      if (comment) fd.append("comment", comment);
      return request("/api/photo-report", { method: "POST", body: fd });
    },

    getPoints: function () { return request("/api/points"); },
    getRoutes: function (agentName, date) {
      return request("/api/routes?agent_name=" + encodeURIComponent(agentName) + "&date=" + encodeURIComponent(date));
    },
    addRoute: function (agentName, date, name, address, time) {
      return request("/api/routes", { method: "POST", body: { agent_name: agentName, date: date, name: name, address: address, time: time } });
    },
    deleteRoute: function (id, agentName, date) {
      return request("/api/routes/" + encodeURIComponent(id) + "?agent_name=" + encodeURIComponent(agentName) + "&date=" + encodeURIComponent(date), { method: "DELETE" });
    },
    getTasks: function (agentName) {
      return request("/api/tasks" + (agentName ? "?agent_name=" + encodeURIComponent(agentName) : ""));
    },
    addTask: function (agentName, title, description, dueDate) {
      return request("/api/tasks", { method: "POST", body: { agent_name: agentName, title: title, description: description, due_date: dueDate } });
    },
    updateTask: function (id, agentName, status) {
      return request("/api/tasks/" + encodeURIComponent(id), { method: "PATCH", body: { agent_name: agentName, status: status } });
    },
    assignIncident: function (id) {
      return request("/api/incidents/" + encodeURIComponent(id) + "/assign", { method: "POST", body: {} });
    },
    getIncidents: function () { return request("/api/incidents"); },
    getVisits: function () { return request("/api/visits"); },
    getEquipmentChecks: function () { return request("/api/equipment/checks"); },
    getPhotoReports: function () { return request("/api/photo-reports"); },
    getHealth: function () { return request("/api/health"); },
    seedDemoData: function () { return request("/api/demo/seed", { method: "POST", body: {} }); },
    getAnalytics: function () { return request("/api/analytics"); },
    getAiAnalytics: function (refresh) {
      return request("/api/analytics/ai" + (refresh ? "?refresh=1" : ""));
    },
    aiStatus: function () { return request("/api/ai/status"); },
    reset: function () { return request("/api/reset", { method: "POST", body: {} }); },

    // history — массив [{role: "user"|"assistant", content: "..."}] (необязательно)
    chat: function (message, history) {
      return request("/api/chat", { method: "POST", body: { message: message, history: history || [] } });
    },

    // Периодический опрос (для дашборда). Возвращает функцию остановки.
    // MilkAPI.poll(function () { return MilkAPI.getIncidents(); }, 2500, render);
    poll: function (fetcher, intervalMs, onData, onError) {
      var stopped = false;
      async function tick() {
        if (stopped) return;
        try { onData(await fetcher()); }
        catch (e) { if (onError) onError(e); }
        if (!stopped) setTimeout(tick, intervalMs || 2500);
      }
      tick();
      return function () { stopped = true; };
    },

    /* ---------- мелкие помощники для интерфейса ---------- */

    // Безопасно превращает ответ ИИ (с **жирным** и переносами строк) в HTML для чата
    formatText: function (text) {
      var s = String(text)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      s = s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
      return s.replace(/\n/g, "<br>");
    },

    // Всплывающее уведомление (type: "success" | "error" | "info")
    toast: function (message, type) {
      var colors = { success: "#16a34a", error: "#dc2626", info: "#2563eb" };
      var el = document.createElement("div");
      el.textContent = message;
      el.style.cssText = "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);" +
        "z-index:9999;padding:12px 18px;border-radius:12px;color:#fff;font:500 14px system-ui,sans-serif;" +
        "box-shadow:0 8px 24px rgba(0,0,0,.25);max-width:90vw;text-align:center;" +
        "background:" + (colors[type] || colors.info);
      document.body.appendChild(el);
      setTimeout(function () { el.remove(); }, 3200);
    }
  };

  global.MilkAPI = MilkAPI;
})(window);
