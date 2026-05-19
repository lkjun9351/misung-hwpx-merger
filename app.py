"""
HWPX 보고서 합치기 서비스
- POST /merge: 여러 hwpx 파일을 받아 1개로 합쳐서 반환

배포: Render Free Tier
"""
import os
import io
import tempfile
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from hwpx import HwpxDocument

app = Flask(__name__)
CORS(app)  # 모든 도메인 허용 (운영 시 misungmpro-energy.kr 만 허용 권장)

# API 키 (간단한 보안)
API_KEY = os.environ.get('MERGE_API_KEY', 'misung-default-key-CHANGE-ME')


@app.route('/')
def index():
    """헬스체크 / 상태 확인"""
    return jsonify({
        'service': 'misung-hwpx-merger',
        'status': 'running',
        'version': '1.0.0'
    })


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/merge', methods=['POST'])
def merge():
    """
    여러 hwpx 파일을 받아 1개로 합쳐서 반환
    
    POST multipart/form-data:
      - api_key: 인증 키
      - files: 여러 hwpx 파일 (순서대로 합쳐짐)
      - filename: 결과 파일명 (선택, 기본값 merged.hwpx)
    
    Returns:
      합쳐진 hwpx 파일 (application/x-hwpx)
    """
    # 1) 인증
    if request.form.get('api_key') != API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # 2) 파일 수신
    files = request.files.getlist('files')
    if len(files) < 1:
        return jsonify({'error': '파일이 없습니다'}), 400
    if len(files) == 1:
        # 1개면 그냥 반환
        return send_file(
            files[0].stream,
            mimetype='application/x-hwpx',
            as_attachment=True,
            download_name=request.form.get('filename', 'merged.hwpx')
        )
    
    # 3) 임시 디렉토리에 저장
    with tempfile.TemporaryDirectory() as tmpdir:
        saved = []
        for i, f in enumerate(files):
            p = os.path.join(tmpdir, f'in_{i:03d}.hwpx')
            f.save(p)
            saved.append(p)
        
        # 4) 첫 번째 파일을 베이스로 열고, 나머지를 섹션으로 추가
        try:
            base_doc = HwpxDocument.open(saved[0])
            
            for src_path in saved[1:]:
                src_doc = HwpxDocument.open(src_path)
                
                # 소스 문서의 모든 섹션을 base에 추가
                # add_section() 으로 새 섹션 만들고, 단락 복사
                for section in src_doc.sections:
                    new_sec = base_doc.add_section()
                    for para in section.paragraphs:
                        new_sec.add_paragraph(para.text())
            
            # 5) 합친 결과를 메모리에 저장
            out_path = os.path.join(tmpdir, 'merged.hwpx')
            base_doc.save_to_path(out_path)
            
            # 6) 클라이언트로 반환
            with open(out_path, 'rb') as f:
                data = f.read()
            
            return send_file(
                io.BytesIO(data),
                mimetype='application/x-hwpx',
                as_attachment=True,
                download_name=request.form.get('filename', 'merged.hwpx')
            )
        
        except Exception as e:
            return jsonify({
                'error': '합치기 실패',
                'detail': str(e)
            }), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
