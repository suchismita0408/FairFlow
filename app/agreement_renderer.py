"""Deterministic clauses and exports from exactly one structured source."""
import io, os
from datetime import date
from decimal import Decimal
from html import escape
from pathlib import Path
from babel.numbers import format_currency, get_currency_precision
from .agreement_schema import SECTIONS, leaves, at, field_spec
from .agreement_validation import digest

def amount(value,currency):
    return format_currency(Decimal(value)/(Decimal(10)**get_currency_precision(currency)),currency,locale='en_US')+' '+currency

def display(value,path,agreement):
    spec=field_spec(path)
    if type(value) is bool:return 'Yes' if value else 'No'
    if spec and spec['type'] in ('date','dates'):
        return date.fromisoformat(value).strftime('%d %b %Y')+' '+at(agreement,'timeline.time_zone','UTC')
    if spec and spec['type']=='money':return amount(value,at(agreement,'payment.currency','USD'))
    if path.startswith('timeline.working_days.') and type(value) is int:return ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][value]
    return str(value)

def render(agreement,agreement_id,number):
    clause_map={};clauses=[];sources={}
    for section,(_,title,clause_id) in SECTIONS.items():
        clauses.append(dict(id=clause_id,title=title,field_path=None,text=title))
        for i,(path,value) in enumerate(leaves(agreement.get(section,{}),section),1):
            if value is None or value=='':continue
            ref=clause_id+'.'+str(i);spec=field_spec(path)
            label=spec['label'] if spec else path.split('.')[-1].replace('_',' ').title()
            # Repeatable references stay visible, so no duplicated prose copies values.
            text=label+': '+display(value,path,agreement)
            clause_map[path]=ref;sources[path]=value
            clauses.append(dict(id=ref,title=label,field_path=path,text=text,source_value=value))
    for i,(path,value) in enumerate(leaves(agreement.get('acknowledgments',{}),'acknowledgments'),1):
        ref='12.'+str(i);clause_map[path]=ref;sources[path]=value
        clauses.append(dict(id=ref,title='Warning acknowledgment',field_path=path,text=path+': '+str(value),source_value=value))
    # Explicit cross-references point to the original clauses, never copied dates/prices.
    for i,m in enumerate(at(agreement,'timeline.milestones',[]) or []):
        covered=[]
        for j,d in enumerate(at(agreement,'scope.deliverables',[]) or []):
            if d.get('id') in (m.get('deliverable_ids') or []):covered.append(clause_map.get(f'scope.deliverables.{j}.name',''))
        clauses.append(dict(id='4.'+str(len(list(leaves(agreement.get('timeline',{}))))+i+1),title='Milestone cross-reference',field_path=None,text='Milestone '+str(i+1)+' covers Deliverable clauses '+', '.join(covered)+'.'))
    sha=digest(agreement)
    html='<article class="agreement-clauses">'+''.join(f'<section id="clause-{escape(c["id"])}"><h3>{escape(c["id"])}. {escape(c["title"])}</h3><p>{escape(c["text"])}</p></section>' for c in clauses)+'</article>'
    return dict(agreement_id=agreement_id,version=number,sha256=sha,agreement=agreement,clause_map=clause_map,clauses=clauses,html=html,sources=sources)

def assert_coverage(export):
    expected={path:value for path,value in leaves(export['agreement']) if value is not None and value!=''}
    if export['sources']!=expected or set(export['clause_map'])!=set(expected):raise ValueError('Agreement field coverage mismatch.')
    for clause in export['clauses']:
        if clause['field_path']:
            path=clause['field_path']
            label=path if path.startswith('acknowledgments.') else clause['title']
            if clause['source_value']!=expected[path] or clause['text']!=label+': '+display(expected[path],path,export['agreement']):raise ValueError('Rendered field does not match structured agreement.')
    if digest(export['agreement'])!=export['sha256']:raise ValueError('Agreement hash mismatch.')

def pdf(export):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import reportlab
    assert_coverage(export)
    paths=[os.getenv('FAIRFLOW_PDF_FONT',''), 'C:/Windows/Fonts/arial.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',str(Path(reportlab.__file__).parent/'fonts/Vera.ttf')]
    font=next((p for p in paths if p and Path(p).exists()),None)
    if font and 'FairFlow' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('FairFlow',font))
    name='FairFlow' if font else 'Helvetica'
    styles=getSampleStyleSheet()
    for style in styles.byName.values():style.fontName=name
    output=io.BytesIO();doc=SimpleDocTemplate(output,title='FairFlow agreement',bottomMargin=65)
    story=[Paragraph('FairFlow agreement',styles['Title'])]
    for c in export['clauses']:
        story.extend([Paragraph(escape(c['id']+'. '+c['text']).replace('\n','<br/>'),styles['BodyText']),Spacer(1,7)])
    def footer(canvas,document):
        canvas.setFont(name,7)
        canvas.drawString(35,38,f"Agreement {export['agreement_id']} | Version {export['version']} | Page {document.page}")
        canvas.drawString(35,26,'SHA-256 '+export['sha256'])
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()
