"""
HWPX 보고서 합치기 서비스 v2
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
        'version': '2.0.0'
    })


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/merge', methods=['POST'])
def merge():
    """
    여러 hwpx 파일을 받아 1개로 합치기.
    - 첫 번째 파일을 base로 사용
    - 나머지 파일들의 section0.xml을 base 끝에 추가
    - BinData(이미지)도 ID 충돌 피해서 병합
    """
    # 인증
    if request.form.get('api_key') != API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    
    files = request.files.getlist('files')
    if len(files) < 1:
        return jsonify({'error': '파일이 없습니다'}), 400
    
    filename = request.form.get('filename', 'merged.hwpx')
    
    if len(files) == 1:
        return send_file(
            files[0].stream,
            mimetype='application/x-hwpx',
            as_attachment=True,
            download_name=filename
        )
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # 모든 파일 저장
        saved = []
        for i, f in enumerate(files):
            p = os.path.join(tmpdir, f'in_{i:03d}.hwpx')
            f.save(p)
            saved.append(p)
        
        try:
            merged_path = merge_hwpx_files(saved, tmpdir)
            
            with open(merged_path, 'rb') as f:
                data = f.read()
            
            return send_file(
                io.BytesIO(data),
                mimetype='application/x-hwpx',
                as_attachment=True,
                download_name=filename
            )
        
        except Exception as e:
            import traceback
            return jsonify({
                'error': '합치기 실패',
                'detail': str(e),
                'traceback': traceback.format_exc()[:2000],
            }), 500


def merge_hwpx_files(file_paths, work_dir):
    """
    HWPX 파일들을 합쳐서 새 hwpx 파일 경로를 반환.
    
    전략:
    1. 첫 번째 파일을 base로 압축 풀기
    2. 두 번째 파일부터:
       a. section{N}.xml 로 추가 (base의 섹션 수 다음 번호)
       b. BinData 이미지를 ID 충돌 피해서 복사 (image1.png → image100.png 식)
       c. section XML 안의 image ID 참조도 업데이트
       d. content.hpf에 새 섹션과 BinData 등록
    3. 다시 zip으로 묶기
    """
    base_dir = os.path.join(work_dir, '_merged')
    os.makedirs(base_dir, exist_ok=True)
    
    # 1) 첫 파일을 base로 압축 풀기
    with zipfile.ZipFile(file_paths[0], 'r') as z:
        z.extractall(base_dir)
    
    # base의 현재 섹션 수 + 이미지 ID 추적
    next_section_idx = count_sections(base_dir)
    next_image_id = get_max_image_id(base_dir) + 1
    
    # 2) 나머지 파일 병합
    for src_path in file_paths[1:]:
        with tempfile.TemporaryDirectory() as src_extract:
            with zipfile.ZipFile(src_path, 'r') as z:
                z.extractall(src_extract)
            
            # 이미지 ID 매핑 (src의 image1 → base의 image{next_image_id})
            src_bindata = os.path.join(src_extract, 'BinData')
            id_map = {}
            
            if os.path.isdir(src_bindata):
                for name in sorted(os.listdir(src_bindata)):
                    m = re.match(r'image(\d+)\.(png|jpe?g)', name, re.I)
                    if m:
                        old_id = m.group(1)
                        ext = m.group(2).lower()
                        new_id = next_image_id
                        id_map[old_id] = str(new_id)
                        
                        # 파일 복사 (새 이름으로)
                        dst_bindata = os.path.join(base_dir, 'BinData')
                        os.makedirs(dst_bindata, exist_ok=True)
                        shutil.copy(
                            os.path.join(src_bindata, name),
                            os.path.join(dst_bindata, f'image{new_id}.{ext}')
                        )
                        next_image_id += 1
            
            # src의 섹션들을 base로 옮기기 (이미지 ID 치환하면서)
            src_contents = os.path.join(src_extract, 'Contents')
            for name in sorted(os.listdir(src_contents)):
                m = re.match(r'section(\d+)\.xml', name)
                if m:
                    xml = open(os.path.join(src_contents, name), encoding='utf-8').read()
                    
                    # 이미지 ID 치환
                    for old_id, new_id in id_map.items():
                        xml = xml.replace(
                            f'binaryItemIDRef="image{old_id}"',
                            f'binaryItemIDRef="image{new_id}"'
                        )
                    
                    dst_section = os.path.join(base_dir, 'Contents', f'section{next_section_idx}.xml')
                    with open(dst_section, 'w', encoding='utf-8') as f:
                        f.write(xml)
                    next_section_idx += 1
    
    # 3) content.hpf 업데이트 (섹션 + BinData 매니페스트)
    update_content_hpf(base_dir)
    
    # 4) 다시 zip으로 묶기
    out_path = os.path.join(work_dir, 'merged.hwpx')
    create_hwpx_zip(base_dir, out_path)
    
    return out_path


def count_sections(base_dir):
    """base의 Contents 폴더에서 section{N}.xml 개수"""
    contents = os.path.join(base_dir, 'Contents')
    if not os.path.isdir(contents):
        return 0
    count = 0
    for name in os.listdir(contents):
        if re.match(r'section\d+\.xml', name):
            count += 1
    return count


def get_max_image_id(base_dir):
    """base의 BinData에서 가장 큰 image ID"""
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
    """content.hpf의 manifest와 spine을 base_dir의 실제 파일에 맞게 업데이트"""
    hpf_path = os.path.join(base_dir, 'Contents', 'content.hpf')
    if not os.path.exists(hpf_path):
        return
    
    hpf = open(hpf_path, encoding='utf-8').read()
    
    # 현재 base의 모든 섹션과 BinData 수집
    sections = []
    contents_dir = os.path.join(base_dir, 'Contents')
    for name in sorted(os.listdir(contents_dir)):
        m = re.match(r'section(\d+)\.xml', name)
        if m:
            sections.append(int(m.group(1)))
    sections.sort()
    
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
    
    # manifest 재구성 (기존 section/BinData 항목 제거 후 새로 추가)
    # manifest에서 section/image 항목 제거
    hpf = re.sub(r'<opf:item\s+id="section\d+"[^/]*/>\s*', '', hpf)
    hpf = re.sub(r'<opf:item\s+id="image\d+"[^/]*/>\s*', '', hpf)
    
    # 새 manifest 항목 만들기
    new_items = ''
    for sec_idx in sections:
        new_items += f'<opf:item id="section{sec_idx}" href="Contents/section{sec_idx}.xml" media-type="application/xml"/>'
    for b in bindata_files:
        new_items += f'<opf:item id="{b["id"]}" href="{b["href"]}" media-type="{b["type"]}"/>'
    
    # </opf:manifest> 앞에 삽입
    hpf = re.sub(r'(</opf:manifest>)', new_items + r'\1', hpf, count=1)
    
    # spine 재구성 (섹션 순서)
    hpf = re.sub(r'<opf:itemref\s+idref="section\d+"\s*/>\s*', '', hpf)
    new_spine = ''
    for sec_idx in sections:
        new_spine += f'<opf:itemref idref="section{sec_idx}"/>'
    hpf = re.sub(r'(</opf:spine>)', new_spine + r'\1', hpf, count=1)
    
    with open(hpf_path, 'w', encoding='utf-8') as f:
        f.write(hpf)


def create_hwpx_zip(base_dir, out_path):
    """base_dir의 내용을 hwpx 포맷의 zip으로 만들기 (mimetype 먼저, 무압축)"""
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
        # mimetype은 반드시 첫 항목, 무압축
        mimetype_path = os.path.join(base_dir, 'mimetype')
        if os.path.exists(mimetype_path):
            zi = zipfile.ZipInfo('mimetype')
            zi.compress_type = zipfile.ZIP_STORED
            with open(mimetype_path, 'rb') as f:
                z.writestr(zi, f.read())
        
        # 나머지 파일 추가
        for root, dirs, files in os.walk(base_dir):
            for fname in files:
                if fname == 'mimetype' and root == base_dir:
                    continue  # 이미 추가함
                full = os.path.join(root, fname)
                arc = os.path.relpath(full, base_dir).replace('\\', '/')
                z.write(full, arc)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
