"""
HWPX 보고서 합치기 서비스 v2.1 (디버그 로그 추가)
"""
import os
import io
import shutil
import tempfile
import zipfile
import re
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

API_KEY = os.environ.get('MERGE_API_KEY', 'CHANGE-ME')


@app.route('/')
def index():
    return jsonify({
        'service': 'misung-hwpx-merger',
        'status': 'running',
        'version': '2.2.0'
    })


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/merge', methods=['POST'])
def merge():
    if request.form.get('api_key') != API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    
    files = request.files.getlist('files')
    print(f"[merge] received {len(files)} files", flush=True)
    for i, f in enumerate(files):
        print(f"[merge] file[{i}]: name={f.filename}", flush=True)
    
    if len(files) < 1:
        return jsonify({'error': '파일이 없습니다'}), 400
    
    filename = request.form.get('filename', 'merged.hwpx')
    
    if len(files) == 1:
        return send_file(files[0].stream, mimetype='application/x-hwpx',
                         as_attachment=True, download_name=filename)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        saved = []
        for i, f in enumerate(files):
            p = os.path.join(tmpdir, f'in_{i:03d}.hwpx')
            f.save(p)
            saved.append(p)
            print(f"[merge] saved: {p} ({os.path.getsize(p)} bytes)", flush=True)
        
        try:
            merged_path = merge_hwpx_files(saved, tmpdir)
            
            print(f"[merge] result size: {os.path.getsize(merged_path)} bytes", flush=True)
            
            with open(merged_path, 'rb') as f:
                data = f.read()
            
            return send_file(io.BytesIO(data), mimetype='application/x-hwpx',
                             as_attachment=True, download_name=filename)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[merge] ERROR: {e}\n{tb}", flush=True)
            return jsonify({'error': '합치기 실패', 'detail': str(e), 'traceback': tb[:2000]}), 500


def merge_hwpx_files(file_paths, work_dir):
    print(f"[merge_hwpx] start, total files={len(file_paths)}", flush=True)
    
    base_dir = os.path.join(work_dir, '_merged')
    os.makedirs(base_dir, exist_ok=True)
    
    # 1) 첫 파일을 base로 압축 풀기
    print(f"[merge_hwpx] extracting base: {os.path.basename(file_paths[0])}", flush=True)
    with zipfile.ZipFile(file_paths[0], 'r') as z:
        z.extractall(base_dir)
    
    # 첫 파일 구조 확인
    contents_dir = os.path.join(base_dir, 'Contents')
    if os.path.isdir(contents_dir):
        files_in_contents = os.listdir(contents_dir)
        print(f"[merge_hwpx] base Contents/: {files_in_contents}", flush=True)
    
    next_section_idx = count_sections(base_dir)
    next_image_id = get_max_image_id(base_dir) + 1
    print(f"[merge_hwpx] base: sections={next_section_idx}, next_image_id={next_image_id}", flush=True)
    
    # 2) 나머지 파일 병합
    for src_path in file_paths[1:]:
        print(f"[merge_hwpx] === processing: {os.path.basename(src_path)} ===", flush=True)
        with tempfile.TemporaryDirectory() as src_extract:
            with zipfile.ZipFile(src_path, 'r') as z:
                z.extractall(src_extract)
            
            # 이미지 ID 매핑
            src_bindata = os.path.join(src_extract, 'BinData')
            id_map = {}
            
            if os.path.isdir(src_bindata):
                bindata_names = sorted(os.listdir(src_bindata))
                print(f"[merge_hwpx]   src BinData: {bindata_names}", flush=True)
                for name in bindata_names:
                    m = re.match(r'image(\d+)\.(png|jpe?g)', name, re.I)
                    if m:
                        old_id = m.group(1)
                        ext = m.group(2).lower()
                        new_id = next_image_id
                        id_map[old_id] = str(new_id)
                        
                        dst_bindata = os.path.join(base_dir, 'BinData')
                        os.makedirs(dst_bindata, exist_ok=True)
                        shutil.copy(
                            os.path.join(src_bindata, name),
                            os.path.join(dst_bindata, f'image{new_id}.{ext}')
                        )
                        next_image_id += 1
            
            print(f"[merge_hwpx]   id_map: {id_map}", flush=True)
            
            # 섹션 복사
            src_contents = os.path.join(src_extract, 'Contents')
            section_files = sorted([n for n in os.listdir(src_contents) if re.match(r'section\d+\.xml', n)])
            print(f"[merge_hwpx]   src sections: {section_files}", flush=True)
            
            for name in section_files:
                xml = open(os.path.join(src_contents, name), encoding='utf-8').read()
                
                # 이미지 ID 치환
                for old_id, new_id in id_map.items():
                    xml = xml.replace(
                        f'binaryItemIDRef="image{old_id}"',
                        f'binaryItemIDRef="image{new_id}"'
                    )
                
                dst_name = f'section{next_section_idx}.xml'
                dst_section = os.path.join(base_dir, 'Contents', dst_name)
                with open(dst_section, 'w', encoding='utf-8') as f:
                    f.write(xml)
                print(f"[merge_hwpx]   {name} -> {dst_name} ({len(xml)} chars)", flush=True)
                print(f"[merge_hwpx]   first 500 chars: {xml[:500]}", flush=True)
                next_section_idx += 1
    
    print(f"[merge_hwpx] final: total sections={next_section_idx}", flush=True)
    
    # 3) content.hpf 업데이트
    print(f"[merge_hwpx] updating content.hpf", flush=True)
    update_content_hpf(base_dir)
    
    # 4) zip 묶기
    # 4) zip 묶기
    out_path = os.path.join(work_dir, 'merged.hwpx')
    print(f"[merge_hwpx] creating zip: {out_path}", flush=True)
    create_hwpx_zip(base_dir, out_path)
    
    # zip 내용 확인
    with zipfile.ZipFile(out_path, 'r') as z:
        names = z.namelist()
        print(f"[merge_hwpx] result zip has {len(names)} files:", flush=True)
        for n in sorted(names):
            info = z.getinfo(n)
            print(f"[merge_hwpx]   {n} ({info.file_size} bytes)", flush=True)
    
    return out_path


def count_sections(base_dir):
    contents = os.path.join(base_dir, 'Contents')
    if not os.path.isdir(contents):
        return 0
    return sum(1 for n in os.listdir(contents) if re.match(r'section\d+\.xml', n))


def get_max_image_id(base_dir):
    bindata = os.path.join(base_dir, 'BinData')
    if not os.path.isdir(bindata):
        return 0
    max_id = 0
    for name in os.listdir(bindata):
        m = re.match(r'image(\d+)\.', name)
        if m:
            max_id = max(max_id, int(m.group(1)))
    return max_id


def update_content_hpf(base_dir):
    hpf_path = os.path.join(base_dir, 'Contents', 'content.hpf')
    if not os.path.exists(hpf_path):
        print(f"[update_hpf] content.hpf 없음", flush=True)
        return
    
    hpf = open(hpf_path, encoding='utf-8').read()
    print(f"[update_hpf] hpf size before: {len(hpf)}", flush=True)
    
    sections = []
    contents_dir = os.path.join(base_dir, 'Contents')
    for name in sorted(os.listdir(contents_dir)):
        m = re.match(r'section(\d+)\.xml', name)
        if m:
            sections.append(int(m.group(1)))
    sections.sort()
    print(f"[update_hpf] sections: {sections}", flush=True)
    
    bindata_files = []
    bindata_dir = os.path.join(base_dir, 'BinData')
    if os.path.isdir(bindata_dir):
        for name in sorted(os.listdir(bindata_dir)):
            m = re.match(r'image(\d+)\.(png|jpe?g)', name, re.I)
            if m:
                bindata_files.append({
                    'id': f'image{m.group(1)}',
                    'href': f'BinData/{name}',
                    'type': 'image/png' if m.group(2).lower() == 'png' else 'image/jpeg'
                })
    print(f"[update_hpf] bindata: {len(bindata_files)} files", flush=True)
    
    # 기존 section/image 항목 제거 (속성 순서 무관)
    before_section_count = len(re.findall(r'<opf:item[^>]*id="section\d+"', hpf))
    before_image_count = len(re.findall(r'<opf:item[^>]*id="image\d+"', hpf))
    print(f"[update_hpf] BEFORE removal: section items={before_section_count}, image items={before_image_count}", flush=True)
    
    # 더 강력한 패턴
    hpf = re.sub(r'<opf:item[^/>]*id="section\d+"[^/>]*/>', '', hpf)
    hpf = re.sub(r'<opf:item[^/>]*id="image\d+"[^/>]*/>', '', hpf)
    
    after_section_count = len(re.findall(r'<opf:item[^>]*id="section\d+"', hpf))
    after_image_count = len(re.findall(r'<opf:item[^>]*id="image\d+"', hpf))
    print(f"[update_hpf] AFTER removal: section items={after_section_count}, image items={after_image_count}", flush=True)
    
    # 새 manifest 항목 만들기
    new_items = ''
    for sec_idx in sections:
        new_items += f'<opf:item id="section{sec_idx}" href="Contents/section{sec_idx}.xml" media-type="application/xml"/>'
    for b in bindata_files:
        new_items += f'<opf:item id="{b["id"]}" href="{b["href"]}" media-type="{b["type"]}" isEmbeded="1"/>'
    
    hpf = re.sub(r'(</opf:manifest>)', new_items + r'\1', hpf, count=1)
    
    # spine 재구성
    before_spine_count = len(re.findall(r'<opf:itemref[^>]*idref="section\d+"', hpf))
    print(f"[update_hpf] BEFORE spine removal: {before_spine_count}", flush=True)
    
    hpf = re.sub(r'<opf:itemref[^/>]*idref="section\d+"[^/>]*/>', '', hpf)
    
    after_spine_count = len(re.findall(r'<opf:itemref[^>]*idref="section\d+"', hpf))
    print(f"[update_hpf] AFTER spine removal: {after_spine_count}", flush=True)
    
    new_spine = ''
    for sec_idx in sections:
        new_spine += f'<opf:itemref idref="section{sec_idx}" linear="yes"/>'
    hpf = re.sub(r'(</opf:spine>)', new_spine + r'\1', hpf, count=1)
    
    with open(hpf_path, 'w', encoding='utf-8') as f:
        f.write(hpf)
    
    print(f"[update_hpf] hpf size after: {len(hpf)}", flush=True)
    print(f"[update_hpf] FINAL hpf content:\n{hpf}", flush=True)


def create_hwpx_zip(base_dir, out_path):
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
        mimetype_path = os.path.join(base_dir, 'mimetype')
        if os.path.exists(mimetype_path):
            zi = zipfile.ZipInfo('mimetype')
            zi.compress_type = zipfile.ZIP_STORED
            with open(mimetype_path, 'rb') as f:
                z.writestr(zi, f.read())
        
        for root, dirs, files in os.walk(base_dir):
            for fname in files:
                if fname == 'mimetype' and root == base_dir:
                    continue
                full = os.path.join(root, fname)
                arc = os.path.relpath(full, base_dir).replace('\\', '/')
                z.write(full, arc)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
