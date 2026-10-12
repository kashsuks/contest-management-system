"""Printable sheet of login slips, one per account, to be cut apart and handed out."""
from fpdf import FPDF

PAGE_FORMAT = 'Letter'  # 215.9 x 279.4 mm
PAGE_WIDTH, PAGE_HEIGHT = 215.9, 279.4
MARGIN = 12
HEADER_HEIGHT = 20
SLIP_HEIGHT = 24
SLIPS_PER_PAGE = int((PAGE_HEIGHT - 2 * MARGIN - HEADER_HEIGHT) // SLIP_HEIGHT)


def _printable(text):
    """The built-in PDF fonts only cover Latin-1; replace anything else instead of failing."""
    return str(text).encode('latin-1', 'replace').decode('latin-1')


def build_credentials_pdf(accounts, contest_name, login_url):
    """Return PDF bytes with one bordered slip per (username, password) pair, SLIPS_PER_PAGE to a page."""
    pdf = FPDF(orientation='P', unit='mm', format=PAGE_FORMAT)
    pdf.set_auto_page_break(False)
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_title(_printable(f'{contest_name} - login slips'))
    pdf.set_creator('Contest Management System')

    slip_width = PAGE_WIDTH - 2 * MARGIN
    for index, (username, password) in enumerate(accounts):
        slot = index % SLIPS_PER_PAGE
        if slot == 0:
            pdf.add_page()
            pdf.set_xy(MARGIN, MARGIN)
            pdf.set_text_color(0)
            pdf.set_font('Helvetica', 'B', 14)
            pdf.cell(slip_width, 7, _printable(contest_name), new_x='LMARGIN', new_y='NEXT')
            pdf.set_font('Helvetica', '', 9)
            pdf.set_text_color(110)
            pdf.cell(slip_width, 5, _printable(f'Sign in at {login_url}  -  cut along the lines'),
                     new_x='LMARGIN', new_y='NEXT')

        top = MARGIN + HEADER_HEIGHT + slot * SLIP_HEIGHT
        pdf.set_draw_color(150)
        pdf.set_line_width(0.3)
        pdf.set_dash_pattern(dash=2, gap=2)
        pdf.rect(MARGIN, top, slip_width, SLIP_HEIGHT)
        pdf.set_dash_pattern()  # back to solid

        for line, (label, value) in enumerate((('Username:', username), ('Password:', password))):
            y = top + 4 + line * 8.5
            pdf.set_xy(MARGIN + 6, y)
            pdf.set_font('Helvetica', '', 12)
            pdf.set_text_color(110)
            pdf.cell(30, 8, label)
            pdf.set_font('Courier', 'B', 16)
            pdf.set_text_color(0)
            pdf.cell(slip_width - 42, 8, _printable(value))

    return bytes(pdf.output())
