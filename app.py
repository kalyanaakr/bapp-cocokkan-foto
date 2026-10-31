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


def load_catalog(directorate):
    all_rows={};cursor='';seen=set()
    for _ in range(100):
        page=api('catalog',directorate=directorate,cursor=cursor)
        for row in page['rows']:all_rows[row['id_bapp_folder']]=row
        next_cursor=page.get('next_cursor')
        if not next_cursor:return all_rows
        if next_cursor in seen or not page['rows']:raise ValueError('Daftar bundle belum lengkap. Coba muat ulang.')
        seen.add(next_cursor);cursor=next_cursor
    raise ValueError('Daftar bundle terlalu besar. Pilih direktorat yang lebih spesifik.')


def save_table(key):
    changes=st.session_state.get(key,{})
    mapping={'ID transaksi':'id_transaksi','NPSN':'npsn','Nama sekolah':'nama_sekolah'}
    updated=[dict(r) for r in st.session_state.draft_rows]
    try:
        for index,delta in changes.get('edited_rows',{}).items():
            row=updated[int(index)]
            for label,field in mapping.items():
                if label in delta:row[field]=str(delta[label] or '')
            if 'Benar' in delta:row['uncertain']=not bool(delta['Benar'])
            if any(k in delta for k in mapping):row['uncertain']=True
        st.session_state.draft_rows=vision.clean_rows(updated)
        st.session_state.draft_version+=1
        st.session_state.pop('comparison',None)
    except (ValueError,IndexError):pass

def decode_backup(raw):
    if len(raw)>2_000_000:raise ValueError()
    data=json.loads(raw)
    if not isinstance(data,dict) or data.get('format')!='bapp-photo-draft-v1':raise ValueError()
    rows=vision.clean_rows(data['rows']);photos={}
    entries=data.get('photos',[])
    if not isinstance(entries,list) or len(entries)>30:raise ValueError()
    for p in entries:
        h=p['hash'];name=p['name']
        if not isinstance(h,str) or len(h)!=64 or any(c not in '0123456789abcdef' for c in h) or not isinstance(name,str) or len(name)>180:raise ValueError()
        photos[h]={'name':name,'complete':p.get('complete') is True}
    return dict(rows=rows,photos=photos)

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
st.markdown('<style>.block-container{padding-top:2rem;max-width:1450px}h1{letter-spacing:-1px}div[data-testid="stMetric"]{background:#f0f5fc;border-radius:14px;padding:16px}@media(max-width:700px){.block-container{padding:1rem!important}h1{font-size:1.8rem!important}button{min-height:44px}} </style>',unsafe_allow_html=True)
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
    st.caption('Baca satu foto setiap kali, maksimal 4 MB. Hasil ditambahkan ke draft.')
    if st.button('Keluar'):
        st.session_state.clear();st.rerun()
    st.caption('created by kalyanaakr')
st.title('Cocokkan foto dengan bundle')
st.write('Temukan BAPP yang terlihat di foto tetapi belum tercatat dalam bundle pilihanmu.')
with st.container(border=True):
    c1,c2=st.columns([4,1])
    directorate=c1.selectbox('Direktorat',['Semua','PAUD','SD','SMP','SMA','SMK','PKPLK','PNFI'])
    refresh=c2.button('Muat ulang daftar',help='Ambil daftar terbaru setelah sinkronisasi BAPP.')
    d='' if directorate=='Semua' else directorate
    credential_id=hashlib.sha256((secret('BAPP_URL')+secret('BAPP_BRIDGE_TOKEN')).encode()).hexdigest()
    if st.session_state.get('catalog_credentials')!=credential_id:
        st.session_state.catalog_pages={};st.session_state.bundle_catalog={};st.session_state.selected_bundle_ids=[]
        st.session_state.catalog_credentials=credential_id
    pages=st.session_state.setdefault('catalog_pages',{})
    if refresh:pages.pop(d,None)
    if d not in pages or time.time()-pages[d]['at']>300:
        try:
            with st.spinner('Memuat daftar bundle…'):
                loaded=load_catalog(d)
            pages[d]={'rows':loaded,'at':time.time()}
        except (ValueError,httpx.HTTPError) as e:
            safe_error(e);st.info('Jika bridge belum diperbarui, pasang bapp_patch_dropdown_bundle.py di Ubuntu terlebih dahulu.');st.stop()
    available=pages[d]['rows']
    catalog=st.session_state.setdefault('bundle_catalog',{})
    catalog.update(available)
    previous=st.session_state.get('selected_bundle_ids',[])
    options=sorted(set(available)|{k for k in previous if k in catalog},key=lambda k:(str(catalog[k].get('nomor_folder') or ''),k))
    def bundle_label(k):
        r=catalog[k]
        return ' · '.join(str(r.get(x) or '—') for x in ('nomor_folder','direktorat','koordinator'))+' · Batch '+str(r.get('batch') or '—')+' · '+k
    fids=st.multiselect('Pilih bundle — ketik nomor langsung di sini',options,format_func=bundle_label,key='selected_bundle_ids',max_selections=10,placeholder='Ketik contoh: 0434, lalu pilih bundle')
    st.caption(f"{len(available):,} bundle tersedia untuk filter ini. Pilih maksimal 10 bundle. Pilihan sebelumnya tetap tersimpan saat direktorat diganti.")
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
st.session_state.setdefault('draft_rows',[])
st.session_state.setdefault('draft_photos',{})
st.session_state.setdefault('draft_version',0)
st.session_state.setdefault('ocr_cache',{})
st.subheader('Tambah foto ke draft')
st.caption('Baca satu foto, periksa hasilnya, lalu tambahkan foto berikutnya. Draft sebelumnya tetap ada.')
mode=st.radio('Sumber foto',['Upload foto','Kamera HP'],horizontal=True)
photo=st.file_uploader('Pilih satu foto',type=['jpg','jpeg','png','webp'],key='one_photo') if mode=='Upload foto' else st.camera_input('Ambil foto daftar BAPP',key='camera_photo')
if st.button('Baca & tambah ke draft',type='primary',width='stretch',disabled=photo is None or not model):
    try:
        if photo.size>4*1024*1024:raise ValueError('Maksimal 4 MB per foto.')
        raw=photo.getvalue();digest=hashlib.sha256(raw).hexdigest()
        if digest in st.session_state.draft_photos:raise ValueError('Foto ini sudah masuk draft. Pilih foto berikutnya.')
        if len(st.session_state.draft_photos)>=30:raise ValueError('Maksimal 30 foto per draft. Unduh hasil lalu mulai draft baru.')
        cache_key=digest+':'+model
        if cache_key not in st.session_state.ocr_cache:
            consume('photo',30,86400)
            with st.spinner('Membaca satu foto… Draft sebelumnya tetap tersimpan.'):
                st.session_state.ocr_cache[cache_key]=vision.extract(secret('GEMINI_API_KEY'),model,raw)
        read=st.session_state.ocr_cache[cache_key]
        if not read['rows']:raise ValueError('Tidak ada baris terbaca. Coba foto lebih dekat; draft lama tetap ada.')
        if len(st.session_state.draft_rows)+len(read['rows'])>500:raise ValueError('Tambahan ini melebihi 500 baris. Tidak ada baris ditambahkan; unduh draft dahulu.')
        source=f"Foto {len(st.session_state.draft_photos)+1} · {photo.name}"[:180]
        addition=[dict(r,source=source,uncertain=True) for r in read['rows']]
        # Store a compact preview only; photos are not part of the downloadable draft.
        preview=vision.jpeg(__import__('base64').b64encode(raw).decode())
        st.session_state.draft_rows.extend(addition)
        st.session_state.draft_photos[digest]={'name':source,'preview':preview,'complete':read['complete']}
        st.session_state.draft_version+=1;st.session_state.pop('comparison',None)
        st.success(f"{len(addition)} baris ditambahkan. Sekarang {len(st.session_state.draft_rows)} baris dalam draft.")
        if not read['complete']:st.warning('Ada bagian foto yang belum terbaca lengkap. Periksa hasil dan tambahkan baris yang terlewat.')
    except (ValueError,httpx.HTTPError) as e:safe_error(e)

st.subheader(f"Draft · {len(st.session_state.draft_rows)} baris")
with st.expander('Foto yang sudah masuk draft'):
    entries=list(st.session_state.draft_photos.values())
    if entries:
        idx=st.selectbox('Foto sumber',range(len(entries)),format_func=lambda i:entries[i]['name'])
        if entries[idx].get('preview'):st.image(entries[idx]['preview'],width='stretch')
        else:st.caption('Pratinjau foto tidak disertakan dalam file cadangan draft.')
    else:st.caption('Belum ada foto. Kamu juga bisa menambah baris manual.')
view=st.radio('Tampilan pemeriksaan',['Kartu — nyaman di HP','Tabel — layar lebar'],horizontal=True)
st.caption('Periksa ID dan NPSN, lalu tandai Benar. Identitas yang berulang tetap terlihat dan tidak dihitung ganda saat pencocokan.')
if st.session_state.draft_rows:
    if view.startswith('Kartu'):
        idx=st.selectbox('Pilih baris untuk diperiksa',range(len(st.session_state.draft_rows)),format_func=lambda i:f"{i+1}. {st.session_state.draft_rows[i]['id_transaksi'] or 'ID belum terbaca'} · {st.session_state.draft_rows[i]['nama_sekolah']}")
        r=st.session_state.draft_rows[idx]
        st.caption(r['source'])
        with st.form('row_'+str(st.session_state.draft_version)+'_'+str(idx)):
            tx=st.text_input('ID transaksi',value=r['id_transaksi'],max_chars=80)
            npsn=st.text_input('NPSN',value=r['npsn'],max_chars=30)
            school=st.text_input('Nama sekolah',value=r['nama_sekolah'],max_chars=250)
            correct=st.checkbox('Identitas ini sudah saya periksa',value=not r['uncertain'])
            save=st.form_submit_button('Simpan koreksi',type='primary',width='stretch')
            delete=st.form_submit_button('Hapus baris ini',width='stretch')
        if save or delete:
            try:
                if delete:st.session_state.draft_rows.pop(idx)
                else:st.session_state.draft_rows[idx]=vision.clean_rows([dict(id_transaksi=tx,npsn=npsn,nama_sekolah=school,source=r['source'],uncertain=not correct)])[0]
                st.session_state.draft_version+=1;st.session_state.pop('comparison',None);st.rerun()
            except ValueError as e:safe_error(e)
        st.caption('Tekan Simpan koreksi sebelum berpindah baris atau menambah foto.')
    else:
        key='draft_editor_'+str(st.session_state.draft_version)
        frame=pd.DataFrame([{'Benar':not r['uncertain'],'ID transaksi':r['id_transaksi'],'NPSN':r['npsn'],'Nama sekolah':r['nama_sekolah'],'Sumber':r['source']} for r in st.session_state.draft_rows])
        st.data_editor(frame,hide_index=True,width='stretch',key=key,on_change=save_table,args=(key,),disabled=['Sumber'],column_config={'Benar':st.column_config.CheckboxColumn('Benar'),'NPSN':st.column_config.TextColumn('NPSN')})
        st.caption('Koreksi tabel tersimpan setelah sel selesai diedit. Jika ID/NPSN diubah, konfirmasi Benar kembali.')
with st.expander('Tambah baris manual'):
    with st.form('manual_draft'):
        tx=st.text_input('ID transaksi baru',max_chars=80)
        ns=st.text_input('NPSN baru',max_chars=30)
        name=st.text_input('Nama sekolah baru',max_chars=250)
        if st.form_submit_button('Tambah baris'):
            try:
                if len(st.session_state.draft_rows)>=500:raise ValueError('Maksimal 500 baris.')
                st.session_state.draft_rows.extend(vision.clean_rows([dict(id_transaksi=tx,npsn=ns,nama_sekolah=name,source='Manual',uncertain=True)]))
                st.session_state.draft_version+=1;st.session_state.pop('comparison',None);st.rerun()
            except ValueError as e:safe_error(e)
with st.expander('Simpan / pulihkan draft'):
    st.info('Draft aktif tersimpan selama sesi ini. Unduh cadangan sebelum menutup atau me-refresh halaman. Foto asli tidak ikut di cadangan.')
    backup={'format':'bapp-photo-draft-v1','rows':st.session_state.draft_rows,'bundles':[catalog[f] for f in fids],'photos':[{'hash':h,'name':r['name'],'complete':r['complete']} for h,r in st.session_state.draft_photos.items()]}
    st.download_button('Unduh cadangan draft',json.dumps(backup,ensure_ascii=False,indent=2).encode(),file_name='draft_bapp.json',mime='application/json',width='stretch')
    uploaded=st.file_uploader('Pulihkan file draft JSON',type=['json'],key='restore_json')
    st.caption('Pemulihan mengganti isi draft. Bundle pembanding tetap pilihan saat ini; pilih ulang sesuai cadangan jika perlu.')
    consent=st.checkbox('Ganti draft saat ini dengan cadangan')
    if st.button('Pulihkan draft',disabled=uploaded is None or not consent):
        try:
            restored=decode_backup(uploaded.getvalue())
            st.session_state.draft_rows=restored['rows'];st.session_state.draft_photos=restored['photos']
            st.session_state.draft_version+=1;st.session_state.pop('comparison',None);st.rerun()
        except (ValueError,TypeError,KeyError) as e:st.error('File draft tidak valid. Draft sekarang tidak diubah.')
with st.expander('Mulai draft baru'):
    reset=st.checkbox('Saya sudah menyimpan hasil/cadangan yang diperlukan')
    if st.button('Kosongkan draft',disabled=not reset):
        st.session_state.draft_rows=[];st.session_state.draft_photos={};st.session_state.ocr_cache={};st.session_state.draft_version+=1;st.session_state.pop('comparison',None);st.rerun()
rows=st.session_state.draft_rows
signature=hashlib.sha256(json.dumps([sorted(fids),rows],sort_keys=True).encode()).hexdigest()
if st.session_state.get('comp_sig')!=signature:st.session_state.pop('comparison',None)
if st.button('Cocokkan seluruh draft',type='primary',width='stretch',disabled=not rows):
    try:
        with st.spinner('Mencocokkan draft dengan seluruh bundle pilihan…'):result=compare_many(fids,rows)
        st.session_state.comparison=result;st.session_state.comp_sig=signature
    except (ValueError,httpx.HTTPError) as e:safe_error(e)
comparison=st.session_state.get('comparison')
if comparison:
    st.divider();st.subheader('Hasil pencocokan')
    labels={'OUTSIDE':'Di luar semua bundle pilihan','MATCH':'Cocok','REVIEW':'Perlu diperiksa','NOT_FOUND':'Tidak ada di master','DUPLICATE':'Duplikat'}
    for code,label in labels.items():st.write(f"**{label}:** {comparison['counts'][code]}")
    for w in comparison['bundle']['warnings']:st.warning(w)
    table=[]
    for r in comparison['rows']:
        master=r.get('master') or {}
        table.append({'Status':labels[r['status']],'ID transaksi':r['id_transaksi'],'NPSN':r['npsn'],'Sekolah':master.get('nama_sekolah') or r['nama_sekolah'],'Cocok di bundle pilihan':r['selected_bundles'],'Bundle di master':master.get('bundles') or '', 'Foto':r['source'],'Catatan':r['note'],'Kandidat NPSN':', '.join(x['id_transaksi'] for x in r['candidates'])})
    selected=st.selectbox('Tampilkan hasil',['Semua',*labels.values()])
    visible=[r for r in table if selected=='Semua' or r['Status']==selected]
    if view.startswith('Kartu'):
        page=st.number_input('Halaman hasil (20 baris per halaman)',min_value=1,max_value=max(1,(len(visible)+19)//20),step=1)
        for item in visible[(page-1)*20:page*20]:
            with st.container(border=True):
                st.write(item['Status']);st.write('**'+item['ID transaksi']+'**');st.write(item['Sekolah'])
                st.caption('NPSN: '+item['NPSN']+' · '+item['Foto'])
                if item['Cocok di bundle pilihan']:st.write(item['Cocok di bundle pilihan'])
                if item['Catatan']:st.caption(item['Catatan'])
    else:st.dataframe(pd.DataFrame(visible),hide_index=True,width='stretch')
    with st.expander('Anggota bundle yang belum teridentifikasi di foto'):
        st.caption('Belum teridentifikasi bukan berarti hilang. Foto bisa belum lengkap atau bacaan belum dikonfirmasi.')
        st.dataframe(pd.DataFrame(comparison['unseen']),hide_index=True,width='stretch')
    out=io.StringIO();writer=csv.DictWriter(out,fieldnames=list(table[0]) if table else ['Status']);writer.writeheader()
    for row in table:writer.writerow({k:("'"+str(v) if str(v).startswith(('=','+','-','@','\t','\r')) else v) for k,v in row.items()})
    st.download_button('Unduh hasil CSV',out.getvalue().encode('utf-8-sig'),file_name='hasil_cocokkan_bapp.csv',mime='text/csv')
st.caption('Hanya membaca data BAPP. Tidak mengubah anggota bundle atau membuat draft revisi. Hasil sesi tidak disimpan permanen.')
