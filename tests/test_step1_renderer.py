import copy
import io,re
import pytest
from app.agreement_examples import example_agreement
from app.agreement_renderer import render, assert_coverage, pdf, amount
from app.agreement_validation import digest

def test_all_fields_and_canonical_hash_coverage():
    a=example_agreement();a['attachments']['links']=[{'label':'Design','url':'https://example.com/design','binding':False}]
    a['acknowledgments']={'warning1':{'client':'Read and understood.','freelancer':'Confirmed.'}}
    result=render(a,'test-agreement',1);assert_coverage(result)
    assert result['sha256']==digest(copy.deepcopy(a))
    assert len(result['sources'])==len(result['clause_map'])
    assert 'Deliverable clauses' in result['html']
    broken=copy.deepcopy(result);broken['clauses'][1]['source_value']='fake'
    with pytest.raises(ValueError):assert_coverage(broken)
    broken=copy.deepcopy(result);broken['agreement']['payment']['total_minor']+=1
    with pytest.raises(ValueError):assert_coverage(broken)

def test_single_source_numbers_dates_and_no_injected_html():
    a=example_agreement();a['basics']['background']='<script>alert(1)</script>'
    export=render(a,'test',2)
    assert '<script>' not in export['html']
    assert '₹1,000.00 INR' in export['html']
    assert amount(100,'JPY')=='¥100 JPY'
    assert amount(1234,'BHD').endswith('1.234 BHD')
    assert_coverage(export)
    export['clauses'][next(i for i,c in enumerate(export['clauses']) if c['field_path']=='payment.total_minor')]['text']='Total: 999'
    with pytest.raises(ValueError):assert_coverage(export)

def test_pdf_is_generated_from_same_clauses_with_footer():
    from pypdf import PdfReader
    export=render(example_agreement(),'agreement-id',3)
    output=pdf(export)
    assert output.startswith(b'%PDF-') and len(output)>10000
    pages=PdfReader(io.BytesIO(output)).pages
    text=' '.join(p.extract_text() for p in pages)
    normalized=lambda s:re.sub(r'\s+','',s)
    for clause in export['clauses']:
        assert normalized(clause['id']+'. '+clause['text']) in normalized(text),clause
    for page in pages:
        assert export['sha256'] in page.extract_text()
        assert 'agreement-id | Version 3' in page.extract_text()
