from urllib.parse import urlsplit
import httpx

def request(base,token,op,**params):
    u=urlsplit(base)
    if u.scheme!='https' or not u.netloc or u.username or u.password or u.query or u.fragment:raise ValueError('BAPP_URL harus alamat HTTPS tanpa username/query.')
    if len(token)<40 or token.startswith('GANTI'):raise ValueError('Atur BAPP_BRIDGE_TOKEN di Secrets.')
    with httpx.Client(timeout=25,follow_redirects=False) as client:
        r=client.post(base.rstrip('/')+'/integrasi/cocokkan-streamlit',headers={'Authorization':'Bearer '+token},json={'op':op,**params})
    if r.status_code!=200:raise ValueError({403:'Token BAPP tidak sesuai.',404:'Bridge BAPP belum terpasang.',400:'Permintaan tidak valid atau bundle tidak ditemukan.'}.get(r.status_code,'BAPP gagal merespons: HTTP '+str(r.status_code)))
    try:return r.json()
    except ValueError:raise ValueError('Respons BAPP bukan JSON. Periksa pemasangan bridge.') from None
