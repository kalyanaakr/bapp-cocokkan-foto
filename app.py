import csv,hashlib,hmac,io,json,time,threading
import pandas as pd
import streamlit as st
import httpx
import bapp_client,vision
def classify(rows,master,members):
    by_id={r['id_transaksi']:r for r in master}
    by_npsn={}
    for item in master:by_npsn.setdefault(item.get('npsn'),[]).append(item)
    member_ids={r['id_transaksi'] for r in members}
    seen=set();matched=set();result=[]
    for item in vision.clean_rows(rows):
        tx=item['id_transaksi'];found=by_id.get(tx);reason='';status='REVIEW'
        if item['uncertain']:reason='Bacaan belum dikonfirmasi.'
        elif not tx:reason='ID transaksi kosong. Cocokkan kandidat NPSN terlebih dahulu.'
        elif not found:reason='ID belum ditemukan di Data Master.';status='NOT_FOUND'
        elif item['npsn'] and item['npsn']!=str(found.get('npsn') or ''):reason='ID cocok tetapi NPSN berbeda.'
        elif tx in seen:status='DUPLICATE';reason='ID muncul lebih dari sekali; tidak dihitung ulang.'
        else:
            seen.add(tx)
            if tx in member_ids:status='MATCH';matched.add(tx)
            else:status='OUTSIDE'
        candidates=by_npsn.get(item['npsn'],[]) if item['npsn'] else []
        result.append(dict(item,status=status,note=reason,master=found,candidates=candidates[:5]))
    missing=[r for r in members if r['id_transaksi'] not in matched]
    order={'OUTSIDE':0,'REVIEW':1,'NOT_FOUND':2,'DUPLICATE':3,'MATCH':4}
    result.sort(key=lambda r:order[r['status']])
    return dict(rows=result,unseen=missing,counts={k:sum(r['status']==k for r in result) for k in order})

def combine_snapshots(snapshots):
    members={};membership={};warnings=[]
    for snapshot in snapshots:
        h=snapshot['header'];label=str(h['nomor_folder'])+' ['+str(h['id_bapp_folder'])+']'
        warnings.extend(label+': '+w for w in snapshot['warnings'])
        for row in snapshot['members']:
            tx=row['id_transaksi'];members.setdefault(tx,dict(row));membership.setdefault(tx,[])
            if label not in membership[tx]:membership[tx].append(label)
    for tx,row in members.items():row['Bundle pilihan']=', '.join(membership[tx])
    return dict(members=list(members.values()),warnings=warnings,membership=membership)

def compare_many(fids,rows):
    # First response supplies master identities for ALL input rows, regardless of membership.
    first=api('compare',fid=fids[0],rows=rows)
    snapshots=[first['bundle']]+[api('bundle',fid=fid) for fid in fids[1:]]
    combined=combine_snapshots(snapshots);master={}
    for row in first['rows']:
        for m in ([row['master']] if row.get('master') else [])+row.get('candidates',[]):master[m['id_transaksi']]=m
    result=classify(rows,list(master.values()),combined['members'])
    for row in result['rows']:row['selected_bundles']=', '.join(combined['membership'].get(row['id_transaksi'],[]))
    return dict(result,bundle=combined)

st.set_page_config(page_title='Cocokkan BAPP',page_icon='📋',layout='wide')
def secret(k):
    try:return str(st.secrets.get(k,''))
    except Exception:return ''
@st.cache_resource
def ledger():return {'lock':threading.Lock(),'login':[],'photo':[]}
def consume(kind,limit,seconds):
    x=ledger();now=time.time()
    with x['lock']:
        x[kind]=[t for t in x[kind] if now-t<seconds]
        if len(x[kind])>=limit:raise ValueError('Batas percobaan tercapai. Coba lagi nanti.')
        x[kind].append(now)
def api(op,**kwargs):return bapp_client.request(secret('BAPP_URL'),secret('BAPP_BRIDGE_TOKEN'),op,**kwargs)
def safe_error(e):st.error(str(e) if isinstance(e,ValueError) else 'Koneksi gagal atau timeout. Coba lagi setelah memeriksa konfigurasi.')
password=secret('APP_PASSWORD')
if len(password)<16 or password.startswith('GANTI'):
    st.title('Cocokkan BAPP');st.info('Atur APP_PASSWORD acak minimal 16 karakter di Streamlit Secrets. Petunjuk ada di README.');st.stop()
fingerprint=hashlib.sha256(password.encode()).hexdigest()
if st.session_state.get('auth')!=fingerprint:
    _,col,_=st.columns([1,2,1])
    with col:
        st.title('Cocokkan BAPP');st.caption('Ruang kerja Admin · foto ke data bundle')
        with st.form('login'):
            entered=st.text_input('Password aplikasi',type='password')
            submit=st.form_submit_button('Masuk',type='primary')
        if submit:
            try:
                consume('login',20,300)
                if hmac.compare_digest(entered.encode(),password.encode()):st.session_state.auth=fingerprint;st.rerun()
                else:st.error('Password tidak sesuai.')
            except ValueError as e:safe_error(e)
    st.stop()
st.markdown('<style>.block-container{padding-top:2rem;max-width:1450px}h1{letter-spacing:-1px}div[data-testid="stMetric"]{background:#f0f5fc;border-radius:14px;padding:16px}</style>',unsafe_allow_html=True)
with st.sidebar:
    st.subheader('Koneksi aplikasi')
    if st.button('Tes koneksi BAPP'):
        try:api('ping');st.success('BAPP terhubung.')
        except (ValueError,httpx.HTTPError) as e:safe_error(e)
    if st.button('Tes Gemini & muat model'):
        try:
            if not secret('GEMINI_API_KEY') or secret('GEMINI_API_KEY').startswith('GANTI'):raise ValueError('Isi GEMINI_API_KEY di Secrets.')
            st.session_state.models=vision.models(secret('GEMINI_API_KEY'));st.success('Daftar model dapat diakses. Pembacaan foto tetap perlu diuji.')
        except (ValueError,httpx.HTTPError) as e:safe_error(e)
    choices=st.session_state.get('models',[])
    configured=secret('GEMINI_MODEL')
    if configured and configured not in choices:choices=[configured]+choices
    model=st.selectbox('Model pembaca foto',choices,index=0 if choices else None)
    st.caption('Foto dikirim ke Google Gemini saat kamu menekan Baca foto. Maksimal 5 foto, 4 MB per foto.')
    if st.button('Keluar'):
        st.session_state.clear();st.rerun()
    st.caption('created by kalyanaakr')
st.title('Cocokkan foto dengan bundle')
st.write('Temukan BAPP yang terlihat di foto tetapi belum tercatat dalam bundle pilihanmu.')
with st.container(border=True):
    with st.form('bundle_search_form'):
        c1,c2,c3=st.columns([1,3,1])
        directorate=c1.selectbox('Direktorat',['Semua','PAUD','SD','SMP','SMA','SMK','PKPLK','PNFI'])
        q=c2.text_input('Nomor penerimaan / bundle',placeholder='Contoh: BAPP-RCV-SMP-0234',max_chars=100)
        search=c3.form_submit_button('Cari bundle',type='primary')
    if search:
        try:st.session_state.search=api('folders',q=q,directorate='' if directorate=='Semua' else directorate)
        except (ValueError,httpx.HTTPError) as e:safe_error(e)
    result=st.session_state.get('search',{'rows':[]})
    catalog=st.session_state.setdefault('bundle_catalog',{})
    for row in result['rows']:catalog[row['id_bapp_folder']]=row
    if result.get('more'):st.caption('50 hasil pencarian ditambahkan. Perjelas nomor untuk menemukan bundle lain; pilihan sebelumnya tetap tersimpan.')
    def bundle_label(k):
        r=catalog[k]
        return ' · '.join(str(r.get(x) or '—') for x in ('nomor_folder','direktorat','koordinator','batch'))+' · '+k
    fids=st.multiselect('Bundle yang dibandingkan',list(catalog),format_func=bundle_label,key='selected_bundle_ids',max_selections=10,placeholder='Pilih satu atau beberapa bundle')
    st.caption('Cari nomor lain untuk menambah pilihan. Klik × pada bundle untuk menghapusnya. Maksimal 10 bundle per pencocokan.')
    if search and not result['rows']:st.info('Tidak ada hasil baru. Pilihan bundle sebelumnya tetap tersedia.')
if not fids:st.info('Pilih sedikitnya satu bundle untuk mulai mencocokkan.');st.stop()
if st.session_state.get('snapshot_ids')!=fids:
    try:
        with st.spinner('Membaca anggota bundle pilihan…'):
            snapshot=combine_snapshots([api('bundle',fid=f) for f in fids])
        st.session_state.snapshot=snapshot;st.session_state.snapshot_ids=list(fids);st.session_state.pop('comparison',None)
    except (ValueError,httpx.HTTPError) as e:safe_error(e);st.stop()
snapshot=st.session_state.snapshot
st.caption(f"{len(fids)} bundle dipilih · {len(snapshot['members'])} ID transaksi unik di database")
for warning in snapshot['warnings']:st.warning(warning)
with st.expander('Lihat gabungan anggota bundle'):st.dataframe(pd.DataFrame(snapshot['members']),hide_index=True,width='stretch')
photos=st.file_uploader('Foto daftar BAPP',type=['jpg','jpeg','png','webp'],accept_multiple_files=True)
if len(photos)>5 or any(p.size>4*1024*1024 for p in photos):st.error('Maksimal 5 foto, masing-masing 4 MB.');st.stop()
sig=hashlib.sha256(b''.join(p.name.encode()+p.getvalue() for p in photos)).hexdigest()
if st.session_state.get('photo_sig')!=sig:
    st.session_state.photo_sig=sig;st.session_state.rows=[];st.session_state.cache={};st.session_state.pop('comparison',None);st.session_state.version=st.session_state.get('version',0)+1
if st.button('Baca foto',type='primary',disabled=not photos or not model):
    assembled=[]
    for p in photos:
        digest=hashlib.sha256(p.getvalue()+model.encode()).hexdigest()
        try:
            if digest not in st.session_state.cache:
                consume('photo',30,86400)
                with st.spinner('Membaca '+p.name+'…'):st.session_state.cache[digest]=vision.extract(secret('GEMINI_API_KEY'),model,p.getvalue())
            read=st.session_state.cache[digest]
            if not read['complete']:st.warning(p.name+': ada bagian yang terpotong/tidak terbaca. Hasil belum lengkap.')
            assembled.extend(dict(r,source=p.name[:180]) for r in read['rows'])
        except (ValueError,httpx.HTTPError) as e:safe_error(e)
    st.session_state.rows=assembled[:500];st.session_state.version+=1
    if len(assembled)>500:st.warning('Hanya 500 baris pertama ditampilkan. Pecah foto menjadi beberapa sesi.')
left,right=st.columns([1,2])
with left:
    st.subheader('Foto sumber')
    if photos:
        photo_index=st.selectbox('Tampilkan foto',range(len(photos)),format_func=lambda i:photos[i].name)
        st.image(photos[photo_index],width='stretch')
    else:st.caption('Unggah foto atau masukkan ID secara manual di tabel.')
with right:
    st.subheader('Periksa hasil bacaan')
    st.caption('Koreksi ID/NPSN lalu centang Benar per baris. NPSN saja tidak otomatis menentukan transaksi.')
    data=[{'Benar':False,'ID transaksi':r['id_transaksi'],'NPSN':r['npsn'],'Nama sekolah':r['nama_sekolah'],'Sumber':r['source']} for r in st.session_state.rows]
    frame=pd.DataFrame(data,columns=['Benar','ID transaksi','NPSN','Nama sekolah','Sumber'])
    frame=frame.astype({'Benar':'bool','ID transaksi':'string','NPSN':'string','Nama sekolah':'string','Sumber':'string'})
    edit=st.data_editor(frame,num_rows='dynamic',hide_index=True,width='stretch',key='editor_'+str(st.session_state.version),column_config={'Benar':st.column_config.CheckboxColumn('Benar',default=False),'NPSN':st.column_config.TextColumn('NPSN'),'ID transaksi':st.column_config.TextColumn('ID transaksi')})
    rows=[{'uncertain':not bool(r['Benar']),'id_transaksi':r['ID transaksi'],'npsn':r['NPSN'],'nama_sekolah':r['Nama sekolah'],'source':r['Sumber']} for r in edit.fillna({'Benar':False,'ID transaksi':'','NPSN':'','Nama sekolah':'','Sumber':''}).to_dict('records')]
    signature=hashlib.sha256(json.dumps([sorted(fids),sig,rows],sort_keys=True).encode()).hexdigest()
    if st.session_state.get('comp_sig')!=signature:st.session_state.pop('comparison',None)
    if st.button('Cocokkan dengan BAPP',type='primary',disabled=not rows):
        try:
            with st.spinner('Mencocokkan seluruh bundle pilihan…'):
                result=compare_many(fids,rows)
            st.session_state.comparison=result;st.session_state.comp_sig=signature
        except (ValueError,httpx.HTTPError) as e:safe_error(e)
comparison=st.session_state.get('comparison')
if comparison:
    st.divider();st.subheader('Hasil pencocokan')
    labels={'OUTSIDE':'Di luar semua bundle pilihan','MATCH':'Cocok','REVIEW':'Perlu diperiksa','NOT_FOUND':'Tidak ada di master','DUPLICATE':'Duplikat'}
    for col,(code,label) in zip(st.columns(5),labels.items()):col.metric(label,comparison['counts'][code])
    for w in comparison['bundle']['warnings']:st.warning(w)
    table=[]
    for r in comparison['rows']:
        master=r.get('master') or {}
        table.append({'Status':labels[r['status']],'ID transaksi':r['id_transaksi'],'NPSN':r['npsn'],'Sekolah':master.get('nama_sekolah') or r['nama_sekolah'],'Cocok di bundle pilihan':r['selected_bundles'],'Bundle di master':master.get('bundles') or '', 'Foto':r['source'],'Catatan':r['note'],'Kandidat NPSN':', '.join(x['id_transaksi'] for x in r['candidates'])})
    selected=st.selectbox('Tampilkan hasil',['Semua',*labels.values()])
    st.dataframe(pd.DataFrame([r for r in table if selected=='Semua' or r['Status']==selected]),hide_index=True,width='stretch')
    with st.expander('Anggota bundle yang belum teridentifikasi di foto'):
        st.caption('Belum teridentifikasi bukan berarti hilang. Foto bisa belum lengkap atau bacaan belum dikonfirmasi.')
        st.dataframe(pd.DataFrame(comparison['unseen']),hide_index=True,width='stretch')
    out=io.StringIO();writer=csv.DictWriter(out,fieldnames=list(table[0]) if table else ['Status']);writer.writeheader()
    for row in table:writer.writerow({k:("'"+str(v) if str(v).startswith(('=','+','-','@','\t','\r')) else v) for k,v in row.items()})
    st.download_button('Unduh hasil CSV',out.getvalue().encode('utf-8-sig'),file_name='hasil_cocokkan_bapp.csv',mime='text/csv')
st.caption('Hanya membaca data BAPP. Tidak mengubah anggota bundle atau membuat draft revisi. Hasil sesi tidak disimpan permanen.')
