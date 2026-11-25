"""Sistema visual do app no padrão OLT Cloud (mesmo design system do OLT Script).

Marca laranja #F2711C, fonte Lato, tema CLARO como padrão (como os clientes usam o OLT Cloud).
Cada cor é um par (claro, escuro); o customtkinter troca sozinho com set_appearance_mode().

Regra de botões (uma ação principal por tela):
  primary   = ação principal (Iniciar, Salvar)            -> laranja OLT Cloud
  secondary = ações comuns (Planilha, Scanner, Testar)    -> neutro com borda
  danger    = destrutivo / parar                           -> vermelho
  ghost     = baixa ênfase (Limpar, Exportar, Abrir pasta) -> só texto
"""
import os

import customtkinter as ctk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
THEME_FILE = os.path.join(HERE, "olt_theme.json")
ASSETS = os.path.join(HERE, "assets")

# ---- tokens (claro, escuro) — static/css/colors.css e colors_dark.css do OLT Cloud
BG = ("#E3E6EA", "#0D1117")
SIDEBAR = ("#FFFFFF", "#10151C")
CARD = ("#FFFFFF", "#121820")
SURFACE_2 = ("#F0F2F5", "#171E27")
HOVER = ("#E4E7EB", "#1F2833")
CARD_BORDER = ("#E1E4E8", "#232C37")
BORDER_STRONG = ("#CDD3DB", "#33404F")
TEXT = ("#101720", "#E4E9F0")
TEXT_2 = ("#3A4452", "#B4BDC9")
MUTED = ("#6A7484", "#8B96A6")

ACCENT = ("#F2711C", "#F2711C")
ACCENT_HOVER = ("#F26202", "#F26202")
ACCENT_TEXT = ("#B84A00", "#FF8534")
ACCENT_SOFT = ("#FDE8D9", "#35261F")

SUCCESS = ("#11914A", "#35C46F")
ERROR = ("#CF3636", "#F2665F")
WARNING = ("#B87503", "#E8A317")
WARNING_SOFT = ("#FFF4D6", "#33291A")
INFO = ("#1678C2", "#4DA3E8")
DANGER_FILL = ("#DB2828", "#DB2828")
DANGER_HOVER = ("#C21F1F", "#C21F1F")

STATUS_COLORS = {          # tag da tabela -> cor do texto
    "success": TEXT,       # sucesso sem cor: o olho vai para as falhas
    "error": ERROR,
    "running": INFO,
    "waiting": WARNING,
    "cancelled": MUTED,
    "pending": MUTED,
}

FONT = "Segoe UI"
FONT_MONO = "Consolas"


ICON_ICO = os.path.join(ASSETS, "app_icon.ico")
ICON_PNG = os.path.join(ASSETS, "app_icon.png")
_icon_photo = None


def apply_icon(win):
    """Ícone do app numa janela (Windows usa o .ico; macOS/Linux o .png)."""
    global _icon_photo
    try:
        if os.name == "nt" and os.path.exists(ICON_ICO):
            win.iconbitmap(ICON_ICO)
        elif os.path.exists(ICON_PNG):
            import tkinter as tk
            if _icon_photo is None:
                _icon_photo = tk.PhotoImage(file=ICON_PNG)
            win.iconphoto(True, _icon_photo)
    except Exception:  # noqa: BLE001 - sem ícone não impede nada
        pass


def _patch_toplevel_icon():
    """O CTkToplevel troca o ícone pelo do customtkinter ~200 ms depois de abrir; reaplica o nosso depois."""
    original = ctk.CTkToplevel.__init__
    if getattr(original, "_gerador_icon", False):
        return

    def init(self, *a, **kw):
        original(self, *a, **kw)
        self.after(300, lambda: apply_icon(self))
    init._gerador_icon = True
    ctk.CTkToplevel.__init__ = init


def setup():
    """Chamar antes de criar a janela: tema claro, cores OLT Cloud, fonte Lato e ícone."""
    global FONT
    if os.name == "nt":
        try:   # barra de tarefas do Windows mostra o ícone do app (e não o do Python)
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GeradorDeBOT.App")
        except Exception:  # noqa: BLE001
            pass
    _patch_toplevel_icon()
    ctk.set_appearance_mode("Light")
    ctk.set_default_color_theme(THEME_FILE)
    files = [os.path.join(ASSETS, f) for f in ("LatoLatin-Regular.ttf", "LatoLatin-Bold.ttf")]
    try:
        if all(os.path.exists(f) for f in files) and all(ctk.FontManager.load_font(f) for f in files):
            FONT = "LatoLatin"
    except Exception:  # noqa: BLE001
        pass


def is_dark():
    return ctk.get_appearance_mode() == "Dark"


def pick(pair):
    return pair[1] if is_dark() else pair[0]


def font(size=13, weight="normal"):
    return ctk.CTkFont(family=FONT, size=size, weight=weight)


def button(master, text, kind="secondary", **kw):
    styles = {
        "primary": dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF",
                        font=font(14, "bold"), height=38),
        "secondary": dict(fg_color=CARD, hover_color=HOVER, text_color=TEXT, border_width=1,
                          border_color=BORDER_STRONG, font=font(13), height=32),
        "danger": dict(fg_color=DANGER_FILL, hover_color=DANGER_HOVER, text_color="#FFFFFF",
                       font=font(14, "bold"), height=38),
        "ghost": dict(fg_color="transparent", hover_color=HOVER, text_color=ACCENT_TEXT,
                      font=font(13), height=30),
    }
    opts = {**styles[kind], **kw}
    return ctk.CTkButton(master, text=text, **opts)


def card(master, **kw):
    return ctk.CTkFrame(master, fg_color=CARD, border_width=1, border_color=CARD_BORDER,
                        corner_radius=10, **kw)


def section_title(master, step, text):
    f = ctk.CTkFrame(master, fg_color="transparent")
    ctk.CTkLabel(f, text=step, width=24, height=24, corner_radius=12, fg_color=ACCENT,
                 text_color="#FFFFFF", font=font(12, "bold")).pack(side="left", padx=(0, 8))
    ctk.CTkLabel(f, text=text, font=font(15, "bold")).pack(side="left")
    return f


def apply_tree_style():
    """ttk.Treeview no padrão OLT Cloud, seguindo o tema ativo."""
    dark = is_dark()
    style = ttk.Style()
    style.theme_use("default")
    bg, fg, head_bg, sel = (("#121820", "#E4E9F0", "#171E27", "#35261F") if dark
                            else ("#FFFFFF", "#101720", "#F0F2F5", "#FDE8D9"))
    sel_fg = "#FF8534" if dark else "#B84A00"
    style.configure("Treeview", background=bg, fieldbackground=bg, foreground=fg, rowheight=30,
                    borderwidth=0, font=(FONT, 10))
    style.map("Treeview", background=[("selected", sel)], foreground=[("selected", sel_fg)])
    style.configure("Treeview.Heading", background=head_bg, foreground=pick(TEXT_2), relief="flat",
                    font=(FONT, 10, "bold"), padding=(8, 6))
    style.map("Treeview.Heading", background=[("active", head_bg)])
    style.configure("Vertical.TScrollbar", background=head_bg, troughcolor=bg, borderwidth=0, arrowsize=12)


def tag_tree(tree):
    for tag, pair in STATUS_COLORS.items():
        tree.tag_configure(tag, foreground=pick(pair))
