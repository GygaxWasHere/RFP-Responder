"""Create templates/Compliance_Response_Template.docx: a plain Milestone-coloured template with placeholders."""
import os
import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Cm

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates", "Compliance_Response_Template.docx")
BLUE = RGBColor(0x00, 0x99, 0xDA)
d = docx.Document()
st = d.styles["Normal"]; st.font.name = "Open Sans"; st.font.size = Pt(10)
for s in d.sections:
    s.left_margin = s.right_margin = Cm(2)
h = d.add_paragraph(); r = h.add_run("{{customer}}"); r.font.size = Pt(26); r.font.color.rgb = BLUE
h = d.add_paragraph(); r = h.add_run("{{project}}"); r.font.size = Pt(16); r.bold = True
d.add_paragraph("Compliance response · {{date}} · {{author}}")
d.add_paragraph("Offered product(s): {{products}}")
d.add_paragraph("{{summary}}")
t = d.add_table(rows=2, cols=5); t.style = "Table Grid"; t.alignment = WD_TABLE_ALIGNMENT.CENTER
heads = ["ID", "Requirement", "Compliance", "Response", "Product"]
vals = ["{{id}}", "{{requirement}}", "{{compliance_label}}", "{{explanation}}", "{{product}}"]
widths = [Cm(1.5), Cm(5.5), Cm(2.4), Cm(6.4), Cm(2.2)]
for i, (hd, v) in enumerate(zip(heads, vals)):
    c = t.rows[0].cells[i]; c.text = hd; c.width = widths[i]
    c.paragraphs[0].runs[0].bold = True; c.paragraphs[0].runs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear"); shd.set(qn("w:fill"), "0076A8"); c._tc.get_or_add_tcPr().append(shd)
    c2 = t.rows[1].cells[i]; c2.text = v; c2.width = widths[i]
for row in t.rows:
    for c in row.cells:
        for p in c.paragraphs:
            for r in p.runs:
                r.font.size = Pt(9)
d.save(OUT)
print(os.path.abspath(OUT))
