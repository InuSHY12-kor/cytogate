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
| 3. 그래프 | Dot, Pseudocolor, Contour, Density, Histogram | 스크린샷 확인 | ⬜ |
| 4. 게이팅 | Polygon, Rectangle, Ellipse, Quadrant, Range, Boolean, 게이트 트리 | `.wsp` 게이트 이벤트 수 일치 | ⬜ |
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

### 4단계에서 처리할 것
- E01 샘플 Time 게이트(1.28–180.48 s): FlowJo 290,172개, 경계값 그대로 계산하면 290,166개(6개 차이).
  추정 원인: FlowJo가 축 끝에 닿은 게이트를 바깥쪽으로 열린 범위로 취급함. 4단계에서 확인한다.
