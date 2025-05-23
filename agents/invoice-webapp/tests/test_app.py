import io
import json
import os
import sys
from unittest import mock
import pytest

# Add path to import the app module
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
import app as invoice_app


@pytest.fixture
def client():
    invoice_app.app.config['TESTING'] = True
    with invoice_app.app.test_client() as client:
        yield client


def make_mock_blob():
    blob = mock.MagicMock()
    blob.upload_from_file.return_value = None
    blob.download_as_bytes.return_value = b"pdfbytes"
    blob.generate_signed_url.return_value = "http://example.com/view"
    return blob


def setup_upload_mocks():
    storage_client = mock.MagicMock()
    bucket = mock.MagicMock()
    blob = make_mock_blob()
    bucket.blob.return_value = blob
    storage_client.bucket.return_value = bucket

    bq_client = mock.MagicMock()
    bq_client.dataset.return_value.table.return_value = mock.sentinel.table

    return storage_client, bucket, blob, bq_client


def test_index_route(client):
    resp = client.get('/')
    assert resp.status_code == 200
    assert b"Upload PDF Invoices" in resp.data


def test_single_upload_success(client):
    storage_client, _, blob, bq_client = setup_upload_mocks()
    with mock.patch.object(invoice_app, 'storage') as storage_mod, \
         mock.patch.object(invoice_app, 'bigquery') as bq_mod, \
         mock.patch.object(invoice_app, 'parse_documentai') as parse_mod, \
         mock.patch.object(invoice_app, 'extract_fields') as extract_mod, \
         mock.patch.object(invoice_app, 'generate_summary') as gen_sum, \
         mock.patch.object(invoice_app, 'check_duplicate') as check_dup:
        storage_mod.Client.return_value = storage_client
        bq_mod.Client.return_value = bq_client
        parse_mod.return_value = mock.MagicMock(entities=[])
        extract_mod.return_value = {'invoice_id': '1', 'supplier_name': 'A'}
        gen_sum.return_value = 'summary'
        check_dup.return_value = []

        data = {'files': (io.BytesIO(b'pdf'), 'test.pdf')}
        resp = client.post('/upload', data=data, content_type='multipart/form-data')
        assert resp.status_code == 200
        assert b"Parsed Results" in resp.data
        assert blob.upload_from_file.called
        assert bq_client.insert_rows_json.called


def test_multiple_upload_success(client):
    storage_client, _, _, bq_client = setup_upload_mocks()
    with mock.patch.object(invoice_app, 'storage') as storage_mod, \
         mock.patch.object(invoice_app, 'bigquery') as bq_mod, \
         mock.patch.object(invoice_app, 'parse_documentai') as parse_mod, \
         mock.patch.object(invoice_app, 'extract_fields') as extract_mod, \
         mock.patch.object(invoice_app, 'generate_summary') as gen_sum, \
         mock.patch.object(invoice_app, 'check_duplicate') as check_dup:
        storage_mod.Client.return_value = storage_client
        bq_mod.Client.return_value = bq_client
        parse_mod.return_value = mock.MagicMock(entities=[])
        extract_mod.return_value = {'invoice_id': '1', 'supplier_name': 'A'}
        gen_sum.return_value = 'summary'
        check_dup.return_value = []

        data = {
            'files': [
                (io.BytesIO(b'pdf1'), 'a.pdf'),
                (io.BytesIO(b'pdf2'), 'b.pdf'),
            ]
        }
        resp = client.post('/upload', data=data, content_type='multipart/form-data')
        assert resp.status_code == 200
        assert resp.data.count(b"Invoice") >= 2


def test_duplicate_handling(client):
    storage_client, _, _, bq_client = setup_upload_mocks()
    with mock.patch.object(invoice_app, 'storage') as storage_mod, \
         mock.patch.object(invoice_app, 'bigquery') as bq_mod, \
         mock.patch.object(invoice_app, 'parse_documentai') as parse_mod, \
         mock.patch.object(invoice_app, 'extract_fields') as extract_mod, \
         mock.patch.object(invoice_app, 'generate_summary') as gen_sum, \
         mock.patch.object(invoice_app, 'check_duplicate') as check_dup:
        storage_mod.Client.return_value = storage_client
        bq_mod.Client.return_value = bq_client
        parse_mod.return_value = mock.MagicMock(entities=[])
        extract_mod.return_value = {'invoice_id': '1', 'supplier_name': 'A'}
        gen_sum.return_value = 'summary'
        check_dup.return_value = [{'invoice_id': '1'}]

        data = {'files': (io.BytesIO(b'pdf'), 'dup.pdf')}
        resp = client.post('/upload', data=data, content_type='multipart/form-data')
        assert resp.status_code == 200
        assert b"Duplicate Detected" in resp.data


def test_upload_error(client):
    with mock.patch.object(invoice_app, 'storage.Client', side_effect=Exception("fail")):
        data = {'files': (io.BytesIO(b'pdf'), 'err.pdf')}
        resp = client.post('/upload', data=data, content_type='multipart/form-data')
        assert resp.status_code == 500


def test_admin_page(client):
    bq_client = mock.MagicMock()
    bq_client.query.side_effect = [iter([mock.Mock(count=3)]), iter([{'vendor': 'A', 'count': 2, 'amount': 10.0}])]
    with mock.patch.object(invoice_app, 'bigquery') as bq_mod:
        bq_mod.Client.return_value = bq_client
        resp = client.get('/admin')
        assert resp.status_code == 200
        assert b"Admin Statistics" in resp.data
        assert b"Total Invoices Parsed" in resp.data


def test_parse_documentai():
    mock_doc = mock.MagicMock()
    mock_client = mock.MagicMock()
    mock_client.process_document.return_value.document = mock_doc
    with mock.patch('app.documentai.DocumentProcessorServiceClient', return_value=mock_client):
        result = invoice_app.parse_documentai(b'data')
        assert result is mock_doc
        mock_client.process_document.assert_called()


def test_extract_fields():
    ent = mock.MagicMock(type_='invoice_id', mention_text='123', confidence=0.8)
    document = mock.MagicMock(entities=[ent])
    fields = invoice_app.extract_fields(document)
    assert fields['invoice_id'] == '123'
    assert fields['document_confidence'] == 0.8


def test_check_duplicate():
    row = {'invoice_id': '1'}
    bq_client = mock.MagicMock()
    bq_client.query.return_value = [row]
    with mock.patch.object(invoice_app, 'bigquery') as bq_mod:
        bq_mod.Client.return_value = bq_client
        rows = invoice_app.check_duplicate('1', 'A')
        assert rows == [row]


def test_generate_summary():
    mock_model = mock.MagicMock()
    mock_model.generate_content.return_value.text = 'sum'
    with mock.patch('app.GenerativeModel', return_value=mock_model), \
         mock.patch('app.vertexai') as vx:
        result = invoice_app.generate_summary({'dummy': 'data'})
        assert result == 'sum'
        vx.init.assert_called()
