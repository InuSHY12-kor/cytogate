# CytoGate 로드맵

개인용 유세포 분석(Flow cytometry) 데스크톱 프로그램. 라이선스 키 없이 동작하며,
결과물은 FlowJo와 같은 양식(통계 정의·표 열 이름·FlowJo 10 `.wsp` 워크스페이스)을 목표로 한다.

- 플랫폼: Windows 데스크톱 (Python 3.11+ / PySide6 / pyqtgraph)
- 엔진: [FlowKit](https://github.com/whitews/FlowKit) (BSD-3) — FCS 읽기, 보정, 변환, 게이팅, `.wsp` 입출력
- 검증 기준: FlowJo 10이 저장한 `.wsp` 안의 게이트별 이벤트 수(`count`)와 비교

| 단계 | 내용 | 완료 기준 | 상태 |
|---|---|---|---|
| 0. 기반 | 저장소, 프로젝트 구조, 샘플 데이터 스크립트, 테스트 실행 | `pytest` 통과 | ✅ |
| 1. FCS 읽기 | FCS 2.0/3.0/3.1, 키워드, 채널(PnN/PnS), 이벤트 행렬 | 샘플 파일의 채널·이벤트 수 일치 | ✅ |
| 2. 보정·변환 | `$SPILLOVER` 보정, Linear / Log / Biex / Logicle / Arcsinh | FlowJo 변환 참조값과 일치 | ✅ |
| 3. 그래프 | Dot, Pseudocolor, Contour, Density, Histogram | 스크린샷 확인 | ✅ |
| 4. 게이팅 | Polygon, Rectangle, Ellipse, Quadrant, Range, Boolean, 게이트 트리 | `.wsp` 게이트 이벤트 수 일치 | ✅ (FlowJo 9 변환 파일 예외, 아래 참고) |
| 5. 통계 | Count, Freq. of Parent/Total/Grandparent, Median, Mean, Geo. Mean, CV, SD, rCV | FlowJo 계산식과 일치 | ⬜ |
| 6. 그룹·일괄 적용 | 그룹, 게이트 일괄 적용, 샘플별 조정 | 여러 파일 일괄 처리 | ⬜ |
| 7. 출력 | Table Editor 형식 CSV/XLSX, `.wsp` 내보내기/불러오기, 그래프 PNG/SVG/PDF, 게이트 이벤트 FCS 내보내기 | FlowJo에서 `.wsp` 열기 | ⬜ |
| 8. 검증 | 실제 FlowJo 분석 결과와 1:1 수치 비교 | 차이 리포트 | ⬜ |
| 9. 배포 | Windows `.exe` (PyInstaller) | 더블클릭 실행 | ⬜ |
| 선택 | tSNE/UMAP, FlowSOM, 세포 주기, 증식 분석 | — | ⬜ |

각 단계는 별도 브랜치에서 작업하고, 테스트 통과 후 `main`에 합친다.

## 단계별 기록

### 1단계 (FCS 읽기)
- `cytogate.io.load_fcs()` / `load_folder()`, 명령줄 `python -m cytogate info <파일|폴더>`
- 키워드는 파일에 적힌 이름 그대로(`$TOT`, `$P1N` …) 읽으며, FlowJo가 `.wsp`에 기록한 값과 모두 일치
- Time 채널은 FlowJo와 같이 `$TIMESTEP`을 곱한 초 단위
- 표준을 어긴 파일(오프셋 불일치, 잘못된 `$TIMESTEP`)도 읽고 경고를 남김

### 2단계 (보정·변환)
- `cytogate.core.Compensation`: FCS 내장 행렬(FlowJo의 "Acquisition-defined"), CSV, FlowJo XML(`spilloverMatrix`) 읽기/쓰기
- 보정 파라미터는 FlowJo처럼 원본 옆에 `Comp-` 이름으로 추가. 파라미터 목록(이름·순서)이 FlowJo의 `$P1N…$P23N` 기록과 일치
- 변환: Linear, Log, Biex, Logicle, ArcSinh. 모두 FlowJo `.wsp`의 XML 요소와 1:1로 읽고 쓰기
- Biex는 FlowJo에서 내보낸 참조표와 4096 채널 중 0.01 채널 이내로 일치
- Logicle은 Moore & Parks(2012) 공식으로 독립 검산
- 축 눈금: 선형 0, 50K, 100K …, 로그형 0, 10³, 10⁴, 10⁵ (+ 2–9 보조 눈금)
- 기본 변환: 산란광 Linear 0–PnR, Time Linear(실제 범위), 형광 Biex(width −10, pos 4.42)

### 3단계 (그래프)
- 데스크톱 창(PySide6 + pyqtgraph): 샘플 목록(#Cells), X/Y 파라미터, 축 변환 선택·세부 설정, 그래프 종류, PNG 내보내기, 파일 끌어다 놓기
- 그래프: Dot, Pseudocolor(기본), Contour(5% 확률 등고선 + 바깥 점), Density, Histogram(Count / Normalized To Mode)
- 축 범위를 벗어난 이벤트는 FlowJo처럼 축 가장자리에 쌓아 표시
- 계산부(`cytogate.plots`)는 화면 없이 테스트, 화면은 offscreen으로 테스트·스크린샷(`scripts/screenshots.py`)

### 4단계 (게이팅)
- 게이트: Rectangle, Range(히스토그램), Polygon, Ellipse, Quadrant(Q1–Q4), Boolean(AND/OR/NOT), 게이트 바깥(eventsInside="0")
- FlowJo처럼 표시 공간(변환 후)에서 판정. 축 끝에 그린 게이트는 그 방향으로 열린 범위
- 화면: 게이트 도구 모음, 마우스로 그리기, 왼쪽 트리에 Freq. of Parent(%)·#Cells, 그래프 위 게이트 윤곽과 비율, 게이트 삭제
- `.wsp` 읽기(게이트·보정·변환·FlowJo가 저장한 수치)를 이번 단계에서 먼저 구현 — 7단계에서 쓰기 추가
- Time: FlowJo는 `$TIMESTEP`이 아니라 샘플별 gain = (`$ETIM` − `$BTIM`) ÷ 저장된 Time 값 범위를 쓴다. `.wsp`에 기록된 gain과 일치, Time 게이트 수치 3개 샘플 모두 일치

#### FlowJo 수치 비교 결과
- FlowJo 10에서 만든 작업공간(사분면+자식 게이트, Biex, ArcSinh, 타원 등 6개 파일): **모든 게이트 수치 정확히 일치**
- FlowJo 9에서 변환된 "DEN" 작업공간: Time은 일치, Singlets 이하에서 Polygon/Rectangle 최대 약 0.3%, Logicle 축의 타원 최대 약 3.2% 차이.
  원인 미확정(FlowKit도 같은 차이). FlowJo의 Logicle 표시 변환이 공식과 다를 가능성이 가장 큼. 8단계에서 FlowJo 10으로 직접 만든 Logicle 작업공간으로 다시 확인 필요
