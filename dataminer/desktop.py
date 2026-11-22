"""Aplicación de escritorio: la plataforma en su propia ventana sin bordes (pywebview + WebView2)."""
from __future__ import annotations

import threading


class Bridge:
    """Controles de ventana expuestos a la interfaz como window.pywebview.api.*"""

    def __init__(self):
        self._window = None
        self._maximized = False

    def minimize(self):
        self._window.minimize()

    def toggle_maximize(self):
        if self._maximized:
            self._window.restore()
        else:
            self._window.maximize()
        self._maximized = not self._maximized
        return self._maximized

    def resize(self, width, height):
        if not self._maximized:
            self._window.resize(max(980, int(width)), max(640, int(height)))

    def close(self):
        self._window.destroy()


def run(port: int = 8787) -> int:
    try:
        import webview
    except ImportError:
        print("Falta pywebview (pip install pywebview). Abriendo en el navegador…")
        from .web import serve
        serve(port, True)
        return 0
    from .web import PLAT, bind

    srv, url = bind(port)
    if srv is not None:
        PLAT.start_scheduler()
        threading.Thread(target=srv.serve_forever, daemon=True, name="dm-http").start()
    bridge = Bridge()
    webview.settings["ALLOW_DOWNLOADS"] = True
    bridge._window = webview.create_window("DataMiner", url + "?shell=desktop", js_api=bridge, width=1440, height=900,
                                          min_size=(980, 640), frameless=True, easy_drag=False,
                                          background_color="#0c0e11", text_select=True)
    webview.start()
    return 0
