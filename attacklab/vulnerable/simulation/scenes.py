"""장면 = 격자 맵 + 움직이는 장애물. Webots 월드(simulation/webots/worlds/<이름>.wbt)는 이 장면으로 만든다 (simulation/build_webots_world.py).

- hospital: 병원 복도 (world.DEFAULT_MAP, 장애물 없음) — 지금까지의 기본 맵
- hospital_busy: 같은 병원 복도에 간호사·병실에서 나오는 환자·로비를 뛰는 아이·서 있는 방문객
- road: 일상 도로. 상가 건물 사이 큰길(차도 4칸, 양쪽 인도 3칸), 옆길과 횡단보도, 주차된 화물차, 가로수.
  사람·공이 오가거나 골목·가게 입구·화물차 사이에서 갑자기 튀어나온다
- plaza: 광장. 여러 방향으로 오가는 인파, 가운데 조형물, 노점, 노점 뒤에서 굴러 나오는 공
- alley: 주택가 골목. 폭 3 m 골목과 안 보이는 모퉁이, 대문에서 뛰어나오는 아이, 빠르게 지나가는 사람
- station: 지하철역 대합실(실내). 기둥 줄, 개찰구의 좁은 틈, 기둥 뒤에서 나오는 사람, 마주 오는 승객
- parking: 주차장. 줄지어 선 차 사이에서 튀어나오는 아이·어른·공, 통로를 걷는 사람
- park: 공원. 잔디·산책로, 나무 뒤에서 나오는 공, 잔디를 가로지르는 아이, 조깅하는 사람

맵 문자: '#' 바깥 벽, 'O' 기둥, 'B' 건물, 'T' 화물차(주차장에서는 차), 'Y' 나무, ',' 인도·산책로, ';' 잔디, '_' 차도,
'-' '!' 차도 + 중앙선, '=' '|' 횡단보도, '.' 실내 바닥, 'S' 시작 (시작 방향은 모두 동쪽 — config.start_angle_deg)
"""

from __future__ import annotations

from dataclasses import dataclass

from simulation.actors import ActorSpec
from simulation.world import DEFAULT_MAP, World

ROAD_MAP: tuple[str, ...] = (
    "################################################",
    "#BBBBBBBB,BBBBBBBBBBBBBBBBBBBBBBB,__!_,BBBBBBBB#",
    "#BBBBBBBB,BBBBBBBBBBBBBBBBBBBBBBB,__!_,BBBBBBBB#",
    "#BBBBBBBB,BBBBBBBBBBBBBBBBBBBBBBB,__!_,BBBBBBBB#",
    "#BBBBBBBB,BBBBBBBBBBBBBBBBBBBBBBB,__!_,BBBBBBBB#",
    "#BBBBBBBB,BBBBBBBBBBBBBBBBBBBBBBB,__!_,BBBBBBBB#",
    "#BBBBBBBB,BBBBBB,BBBBBBBBBB,BBBBB,__!_,BBBBBBBB#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,||||,,,,,,,GG#",
    "#,,S,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,||||,,,,,,,GG#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,||||,,,,,,,GG#",
    "#_________TTTTT__________TTTTTTT__________==___#",
    "#_________TTTTT__________TTTTTTT__________==___#",
    "#--------------TTTTTTT-TTTTT--------------==---#",
    "#______________TTTTTTT_TTTTT______________==___#",
    "#,,,,,,,Y,,,,,,,,,,,,Y,,,,,,,,Y,,,,,,,,,,,,,,,,#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,#",
    "#BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB#",
    "#BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB#",
    "#BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB#",
    "#BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB#",
    "#BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB#",
    "################################################",
)

# 도착지 주행 (사람 운전 모델 tools/goal_trials.py로 맞춘 배치): 튀어나오는 것(popout)은 로봇이 trigger 원에 들어오면
# 경로 바로 옆 가림막(건물 모서리·문·차·기둥) 뒤에서 나와 경로 위에 잠깐 서 있다. 가림막 뒤에서 1~2 m만 나오게 둔다 —
# 멀리서 옆으로 걸어 들어오면 로봇 옆 40~60도에서 코앞에 닿아 정면 루밍(LPLC2)으로는 볼 수 없다.
# trigger 반지름은 '예상 못 한 상황의 반응 시간(1.25~1.5초)인 사람은 W를 떼기 전에 부딪히고, 커넥톰은 반응 시간과 상관없이
# 멈추거나 비켜 가는' 구간의 가운데로 골랐다 (1초인 사람과 커넥톰이 부딪히기 시작하는 거리는 거의 같다).
# 공은 커넥톰(위쪽 시야에 치우친 DNp01)이 잘 못 봐서 경로에 두지 않는다.
# 오가는 사람(loop)은 경로 옆을 지나거나 가로지른다 (로봇에 막히면 2초 뒤 돌아선다 — simulation/actors.py).

# 일상 도로: 북쪽 인도(y=8.5)를 따라 동쪽 끝 도착지(45~46번 열)로. 골목에서 서둘러 나오는 어른(9번 열) → 가게에서 뛰어나오는
# 아이(16) → 카페 문에서 나오는 사람(27) → 옆길 모퉁이 건물 뒤에서 뛰어나오는 아이(33). 큰길 횡단보도는 차도만 건넌다
ROAD_ACTORS: tuple[ActorSpec, ...] = (
    ActorSpec("alley_adult", "adult", ((9.5, 6.0), (9.5, 8.5)), speed=2.0, mode="popout",
              trigger=(9.5, 8.5, 3.9), pause_s=2.5, color=(60, 150, 90)),
    ActorSpec("shop_child", "child", ((16.5, 6.4), (16.5, 8.5)), speed=2.5, mode="popout",
              trigger=(16.5, 8.5, 3.2), pause_s=2.5, color=(230, 190, 40)),
    ActorSpec("cafe_adult", "adult", ((27.5, 6.3), (27.5, 8.5)), speed=2.0, mode="popout",
              trigger=(27.5, 8.5, 3.25), pause_s=2.5, color=(170, 90, 60)),
    ActorSpec("side_kid", "child", ((33.6, 6.4), (34.2, 8.5)), speed=2.5, mode="popout",
              trigger=(34.2, 8.5, 3.15), pause_s=2.5, color=(220, 50, 50)),
    ActorSpec("walker", "adult", ((31.0, 7.4), (23.0, 7.4)), speed=1.2, color=(70, 110, 190)),   # 인도 안쪽에서 마주 옴
    ActorSpec("cross_a", "adult", ((42.4, 9.8), (42.4, 15.6)), speed=1.1, color=(140, 80, 170)),  # 큰길 횡단
    ActorSpec("cross_b", "adult", ((43.6, 15.6), (43.6, 9.8)), speed=1.0, color=(220, 120, 50)),
    ActorSpec("standing", "adult", ((40.5, 7.3),), color=(90, 90, 96)),                          # 서 있는 사람
    ActorSpec("road_child", "child", ((22.5, 14.6), (22.5, 11.5)), speed=2.5, mode="popout",
              trigger=(22.5, 11.5, 5.0), pause_s=2.5, color=(230, 110, 150)),      # 화물차 사이에서 차도로 뛰어나옴
    ActorSpec("south_walker", "adult", ((6.0, 15.4), (40.0, 15.4)), speed=0.9, color=(120, 120, 130)),  # 남쪽 인도
)

PLAZA_MAP: tuple[str, ...] = (
    "####################################",
    "#BBBBB,,BBBBBBBBBBBBBBBBBBBBBBBBBBB#",
    "#BBBBB,,BBBBBBBBBBBBBBBBBBBBBBBBBBB#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,#",
    "#,,,,,,,,,,,,,KK,,,,,,,,,,,,,,,,,,,#",
    "#,,,,,,,Y,,,,,,,,,,,,,,,,,,Y,,,,,,,#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,KK,,,,,,,#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,#",
    "#,S,,,,,,,,,,,,,,OO,,,,,,,,,,,,,,GG#",
    "#,,,,,,,,,,,,,,,,OO,,,,,,,,,,,,,,GG#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,#",
    "#,,,,,,,Y,,,,,,,,,,,,,,,,,,Y,,,,,,,#",
    "#,,,,,,,,,,,,,,,,,,,KK,,,,,,,,,,,,,#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,#",
    "#BBBBBBBBBBBBBBBBBBBBBBBBBBB,,BBBBB#",
    "#BBBBBBBBBBBBBBBBBBBBBBBBBBB,,BBBBB#",
    "####################################",
)

ALLEY_MAP: tuple[str, ...] = (
    "########################################",
    "#BBBBBBBBB,,,BBBBBBBBB,,,BBBBBBBB,,,BBB#",
    "#BBBBBBBBB,,,BBBBBBBBB,,,BBBBBBBB,,,BBB#",
    "#BBBBBBBBB,,,BBBBBBBBB,,,BBBBBBBB,,,BBB#",
    "#BBBBBBBBB,,,BBBB,BBBB,,,BBBBBBBB,,,BBB#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,GG#",
    "#,S,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,GG#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,GG#",
    "#BBBBBBBBB,,,BBBBBBBBB,,,BBBBBBBB,,,BBB#",
    "#BBBBBBBBB,,,BBBBBBBBB,,,BBBBBBBB,,,BBB#",
    "#BBBBBBBBB,,,BBBBBBBBB,,,BBBBBBBB,,,BBB#",
    "#BBBBBBBBB,,,BBBBBBBBB,,,BBBBBBBB,,,BBB#",
    "#BBBBBBBBB,,,BBBBBBBBB,,,BBBBBBBB,,,BBB#",
    "#BBBBBBBBB,,,BBBBBBBBB,,,BBB,BBBB,,,BBB#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,#",
    "#BBBBBBBBB,,,BBBBBBBBB,,,BBBBBBBB,,,BBB#",
    "#BBBBBBBBB,,,BBBBBBBBB,,,BBBBBBBB,,,BBB#",
    "#BBBBBBBBB,,,BBBBBBBBB,,,BBBBBBBB,,,BBB#",
    "#BBBBBBBBB,,,BBBBBBBBB,,,BBBBBBBB,,,BBB#",
    "########################################",
)

STATION_MAP: tuple[str, ...] = (
    "############################################",
    "#......KKK.....KKK............#............#",
    "#.............................#............#",
    "#.............................#............#",
    "#..........................................#",
    "#.....O.....O.....O.....O.....#............#",
    "#.............................#....O...O...#",
    "#.............................#............#",
    "#........O............O.......#............#",
    "#........................................GG#",
    "#.S......................................GG#",
    "#.............................#............#",
    "#...............O.............#............#",
    "#.............................#............#",
    "#.............................#....O...O...#",
    "#.....O.....O.....O.....O.....#............#",
    "#..........................................#",
    "#.............................#............#",
    "#.............................#............#",
    "#.............................#............#",
    "#......KKK.....KKK............#............#",
    "############################################",
)

PARKING_MAP: tuple[str, ...] = (
    "########################################",
    "#BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB#",
    "#BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB#",
    "#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,#",
    "#_TT_TT_TT_TT_TT_TT_TT_TT_TT___TT_TT_TT#",
    "#_TT_TT_TT_TT_TT_TT_TT_TT_TT___TT_TT_TT#",
    "#_TT_TT_TT_TT_TT_TT_TT_TT_TT___TT_TT_TT#",
    "#_TT_TT_TT_TT_TT_TT_TT_TT_TT___TT_TT_TT#",
    "#_TT_TT_TT_TT_TT_TT_TT_TT_TT___TT_TT_TT#",
    "#______________________________________#",
    "#____________________________________GG#",
    "#_S__________________________________GG#",
    "#____________________________________GG#",
    "#______________________________________#",
    "#_TT_TT_TT_TT_TT_TT_TT_TT_TT___TT_TT_TT#",
    "#_TT_TT_TT_TT_TT_TT_TT_TT_TT___TT_TT_TT#",
    "#_TT_TT_TT_TT_TT_TT_TT_TT_TT___TT_TT_TT#",
    "#_TT_TT_TT_TT_TT_TT_TT_TT_TT___TT_TT_TT#",
    "#_TT_TT_TT_TT_TT_TT_TT_TT_TT___TT_TT_TT#",
    "#______________________________________#",
    "#______________________________________#",
    "########################################",
)

PARK_MAP: tuple[str, ...] = (
    "########################################",
    "#;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;#",
    "#;,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,;#",
    "#;,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,;#",
    "#;,,;;;;;;;;;;;;;;;,,;;;;;;;;;;;;;;;,,;#",
    "#;,,;;;;;;;;;;;;;;;,,;;;;;;;;;;;;;;;,,;#",
    "#;,,;;;;Y;;;;;;Y;;;,,;;;;;Y;;;;;;;;;,,;#",
    "#;,,;;;;;;;;;;;;;;;,,;;;;;;;;;;Y;;;;,,;#",
    "#;,,;;;;Y;;;;;;;;;;,,;;;Y;;;;;;;;;;;,,;#",
    "#;,,;;;;;;;;;;;;;;;,,;;;;;;;;;;;;;;;,,;#",
    "#;,S,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,GG;#",
    "#;,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,GG;#",
    "#;,,;KKK;;;;;;;;;;;,,;;;;;KKK;;;;;;;,,;#",
    "#;,,;;;;;;;;;;;;Y;;,,;;;;;;;;;;Y;;;;,,;#",
    "#;,,;;;;;;;;;Y;;;;;,,;;;;;;;;;Y;;;;;,,;#",
    "#;,,;;;;;Y;;;;;;;;;,,;;;Y;;;;;;;;;;;,,;#",
    "#;,,;;;;;;;;;;;;;;;,,;;;;;;;;;;;;;;;,,;#",
    "#;,,;;;;;;;;;;;;;;;,,;;;;;;;;;;;;;;;,,;#",
    "#;,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,;#",
    "#;,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,;#",
    "#;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;#",
    "########################################",
)


# 붐비는 병원: 복도를 따라 오른쪽 아래 끝 도착지로. 병실 문에서 나오는 환자(9번 열) → 아래로 꺾인 복도에서 왼쪽 병실 문으로
# 서둘러 나오는 보호자(11번 줄) → 아래 복도에서 칸막이 벽 뒤에서 뛰어나오는 직원(21번 열). 위 복도를 오가는 간호사, 로비의 사람들
# (실내 영상에서 아이는 키가 카메라 높이와 비슷해 커넥톰이 늦게 봐서, 경로에 들어서는 사람은 어른만 둔다 — 지하철역과 같다)
HOSPITAL_BUSY_ACTORS: tuple[ActorSpec, ...] = (
    ActorSpec("patient", "adult", ((9.5, 4.2), (9.5, 6.3)), speed=2.0, mode="popout",
              trigger=(9.5, 6.3, 3.25), pause_s=2.5, color=(150, 190, 220)),
    ActorSpec("room_guardian", "adult", ((7.2, 11.5), (10.0, 11.5)), speed=2.4, mode="popout",
              trigger=(10.0, 11.5, 3.9), pause_s=2.5, color=(240, 160, 60)),
    ActorSpec("block_staff", "adult", ((21.6, 12.4), (21.6, 14.6)), speed=2.5, mode="popout",
              trigger=(21.6, 14.6, 3.6), pause_s=2.5, color=(230, 110, 150)),
    ActorSpec("nurse", "adult", ((22.5, 5.6), (4.0, 5.6)), speed=1.2, color=(235, 235, 245)),
    ActorSpec("visitor", "adult", ((16.5, 6.3),), color=(120, 90, 70)),
    ActorSpec("lobby_child", "child", ((14.5, 1.5), (22.5, 1.5), (22.5, 9.0), (14.5, 9.0), (14.5, 1.5)), speed=2.2,
              color=(240, 200, 50)),
    ActorSpec("porter", "adult", ((14.0, 3.6), (22.0, 3.6)), speed=1.5, color=(90, 140, 90)),
)

# 광장: 가운데 조형물을 위로 돌아 동쪽 도착지로. 조형물 뒤에서 뛰어나오는 아이 → 노점 뒤에서 뛰어나오는 아이.
# 경로를 가로지르는 사람 둘, 위아래로 오가는 인파, 모여 서 있는 사람 둘
PLAZA_ACTORS: tuple[ActorSpec, ...] = (
    ActorSpec("sculpture_kid", "child", ((19.6, 10.6), (19.6, 7.5)), speed=2.5, mode="popout",
              trigger=(19.6, 7.5, 3.55), pause_s=2.5, color=(240, 200, 50)),
    ActorSpec("stall_kid", "child", ((28.5, 7.2), (28.5, 9.5)), speed=2.5, mode="popout",
              trigger=(28.5, 9.5, 3.6), pause_s=2.5, color=(220, 50, 50)),
    ActorSpec("crowd_a", "adult", ((33.0, 6.3), (3.5, 6.3)), speed=1.3, color=(70, 110, 190)),
    ActorSpec("crowd_b", "adult", ((3.5, 13.0), (33.0, 13.0)), speed=1.0, color=(190, 80, 80)),
    ActorSpec("cross_1", "adult", ((11.5, 3.5), (11.5, 15.5)), speed=1.2, color=(90, 160, 90)),
    ActorSpec("cross_2", "adult", ((24.5, 15.5), (24.5, 3.5)), speed=1.4, color=(160, 90, 170)),
    ActorSpec("standing_1", "adult", ((29.8, 11.3),), color=(100, 100, 110)),
    ActorSpec("standing_2", "adult", ((30.6, 11.9),), color=(200, 140, 90)),
)

# 주택가 골목: 위쪽 골목(y=6.5)을 따라 동쪽 끝 도착지로. 첫 갈림길 모퉁이 담 뒤에서 서둘러 나오는 어른 → 대문에서 뛰어나오는
# 아이 → 두 번째 갈림길 아래 모퉁이에서 자전거처럼 빠르게(3 m/s) 튀어나오는 아이 → 세 번째 갈림길 모퉁이에서 공을 쫓아
# 뛰어나오는 아이.
# 골목 안쪽으로 마주 오는 사람, 아래 골목의 사람들
ALLEY_ACTORS: tuple[ActorSpec, ...] = (
    ActorSpec("corner_adult", "adult", ((10.3, 4.5), (10.8, 6.5)), speed=2.2, mode="popout",
              trigger=(10.8, 6.5, 3.2), pause_s=2.5, color=(60, 150, 90)),
    ActorSpec("gate_child", "child", ((17.5, 4.4), (17.5, 6.5)), speed=2.5, mode="popout",
              trigger=(17.5, 6.5, 3.1), pause_s=2.5, color=(230, 190, 40)),
    ActorSpec("bike_kid", "child", ((22.3, 8.6), (22.8, 6.5)), speed=3.0, mode="popout",
              trigger=(22.8, 6.5, 3.2), pause_s=2.5, color=(40, 40, 50)),
    ActorSpec("ball_chaser", "child", ((33.3, 4.4), (33.8, 6.5)), speed=2.8, mode="popout",
              trigger=(33.8, 6.5, 2.75), pause_s=2.5, color=(220, 50, 50)),
    ActorSpec("oncoming", "adult", ((36.0, 5.4), (26.5, 5.4)), speed=1.2, color=(70, 110, 190)),
    ActorSpec("lower_walker", "adult", ((2.5, 15.5), (37.5, 15.5)), speed=1.0, color=(150, 120, 100)),
    ActorSpec("lower_gate", "child", ((28.5, 13.4), (28.5, 15.5)), speed=2.3, mode="popout",
              trigger=(28.5, 15.5, 3.5), pause_s=2.5, color=(230, 110, 150)),
)

# 지하철역: 대합실 가운데(y=10)를 따라 개찰구 틈을 지나 동쪽 끝 도착지로. 경로 옆 기둥 뒤에서 나오는 사람 셋(9·16·22번 열),
# (실내 영상에서 아이는 키가 카메라 높이보다 낮아 커넥톰이 거의 못 봐서 어른만 둔다)
# 개찰구 벽 너머에서 틈으로 들어와 마주 서는 사람. 경로 옆을 오가는 승객, 개찰구 앞에 서 있는 사람들
STATION_ACTORS: tuple[ActorSpec, ...] = (
    ActorSpec("pillar_man", "adult", ((10.4, 8.3), (10.4, 10.0)), speed=1.8, mode="popout",
              trigger=(10.4, 10.0, 3.25), pause_s=2.5, color=(60, 150, 90)),
    ActorSpec("pillar_student", "adult", ((17.4, 12.7), (17.4, 10.0)), speed=2.2, mode="popout",
              trigger=(17.4, 10.0, 3.3), pause_s=2.5, color=(240, 160, 60)),
    ActorSpec("pillar_woman", "adult", ((23.4, 8.3), (23.4, 10.0)), speed=1.8, mode="popout",
              trigger=(23.4, 10.0, 3.4), pause_s=2.5, color=(190, 80, 120)),
    ActorSpec("gate_rusher", "adult", ((32.5, 8.0), (31.0, 9.7), (29.4, 10.0)), speed=1.8, mode="popout",
              trigger=(29.4, 10.0, 3.65), pause_s=2.5, color=(90, 130, 200)),
    ActorSpec("commuter_a", "adult", ((28.5, 7.4), (3.5, 7.4)), speed=1.3, color=(70, 70, 90)),
    ActorSpec("commuter_b", "adult", ((3.5, 13.6), (27.5, 13.6), (29.5, 16.5), (40.5, 16.5)), speed=1.1,
              color=(180, 90, 60)),
    ActorSpec("rusher", "adult", ((41.5, 4.5), (3.5, 4.5)), speed=1.6, color=(150, 150, 60)),
    ActorSpec("waiting_1", "adult", ((27.0, 7.6),), color=(120, 120, 130)),
    ActorSpec("waiting_2", "adult", ((27.5, 13.0),), color=(200, 170, 120)),
    ActorSpec("station_child", "child", ((20.5, 19.5), (20.5, 14.2)), speed=2.2, color=(240, 200, 50)),
)

# 주차장: 수직으로 세운 차(2×5칸) 두 줄 사이 통로의 오른쪽(y=12.5, 우측 통행)을 따라 동쪽 끝 도착지로. 아래 줄 차 사이
# 틈에서 튀어나오는 아이(7번 열) → 어른(16) → 아이(25) → 보행 통로(28~30번 열) 모퉁이 차 뒤에서 나오는 어른.
# 통로 반대쪽 가장자리를 마주 걷는 사람, 아래 통로의 사람
PARKING_ACTORS: tuple[ActorSpec, ...] = (
    ActorSpec("car_child", "child", ((7.5, 14.8), (7.5, 12.5)), speed=2.5, mode="popout",
              trigger=(7.5, 12.5, 3.15), pause_s=2.5, color=(230, 190, 40)),
    ActorSpec("car_adult", "adult", ((16.5, 15.2), (16.5, 12.5)), speed=2.2, mode="popout",
              trigger=(16.5, 12.5, 3.75), pause_s=2.5, color=(60, 150, 90)),
    ActorSpec("car_child2", "child", ((25.5, 14.8), (25.5, 12.5)), speed=2.8, mode="popout",
              trigger=(25.5, 12.5, 3.2), pause_s=2.5, color=(220, 50, 50)),
    ActorSpec("walkway_adult", "adult", ((28.4, 14.8), (29.0, 12.5)), speed=2.0, mode="popout",
              trigger=(29.0, 12.5, 3.7), pause_s=2.5, color=(150, 120, 100)),
    ActorSpec("aisle_walker", "adult", ((36.0, 9.6), (20.0, 9.6)), speed=1.2, color=(70, 110, 190)),
    ActorSpec("aisle2_walker", "adult", ((3.0, 19.5), (37.0, 19.5)), speed=0.9, color=(120, 120, 130)),
)

# 공원: 가운데 길(y=10.5)을 따라 동쪽 끝 도착지로. 길가 나무 뒤·옆에서 뛰어나오는 아이들(9·17·32번 열)과 사람(25).
# 위·아래 둘레 길의 조깅하는 사람·산책하는 사람, 길가에 서 있는 사람, 잔디에서 노는 아이
PARK_ACTORS: tuple[ActorSpec, ...] = (
    ActorSpec("tree_kid1", "child", ((9.4, 8.6), (9.4, 10.5)), speed=2.5, mode="popout",
              trigger=(9.4, 10.5, 2.9), pause_s=2.5, color=(240, 200, 50)),
    ActorSpec("tree_kid2", "child", ((17.4, 13.4), (17.4, 10.5)), speed=3.0, mode="popout",
              trigger=(17.4, 10.5, 3.1), pause_s=2.5, color=(230, 110, 150)),
    ActorSpec("tree_runner", "adult", ((25.4, 8.6), (25.4, 10.5)), speed=2.0, mode="popout",
              trigger=(25.4, 10.5, 3.25), pause_s=2.5, color=(220, 60, 90)),
    ActorSpec("tree_kid3", "child", ((32.4, 12.7), (32.4, 10.5)), speed=3.0, mode="popout",
              trigger=(32.4, 10.5, 2.75), pause_s=2.5, color=(60, 120, 220)),
    ActorSpec("jogger", "adult", ((2.8, 2.8), (36.8, 2.8)), speed=2.6, color=(200, 80, 60)),
    ActorSpec("path_walker", "adult", ((2.8, 18.8), (36.8, 18.8)), speed=1.1, color=(70, 110, 190)),
    ActorSpec("park_standing", "adult", ((30.0, 11.9),), color=(100, 100, 110)),
    ActorSpec("picnic_child", "child", ((24.5, 16.5), (30.5, 16.5)), speed=1.8, color=(230, 160, 60)),
)

@dataclass(frozen=True)
class Scene:
    name: str
    rows: tuple[str, ...]
    outdoor: bool = False
    actors: tuple[ActorSpec, ...] = ()
    title: str = ""  # 웹 화면의 맵 고르기에 보이는 이름
    route: tuple[tuple[float, float], ...] = ()  # 시작('S')에서 도착지('G')까지 권장 경로 (사람 운전 모델 tools/goal_trials.py)

    def world(self) -> World:
        return World(self.rows, outdoor=self.outdoor)

    @property
    def world_file(self) -> str:
        """simulation/webots/worlds/ 안의 Webots 월드 파일 이름: 기본 맵은 reflexguard.wbt, 나머지는 <이름>.wbt."""
        return "reflexguard.wbt" if self.name == DEFAULT_SCENE else f"{self.name}.wbt"


# 병원 복도: 위 복도를 동쪽으로 → 아래로 꺾인 복도 → 아래 복도를 동쪽으로, 오른쪽 아래 끝 도착지(world.DEFAULT_MAP의 'G')
HOSPITAL_ROUTE: tuple[tuple[float, float], ...] = ((1.5, 6.5), (10.0, 6.5), (10.0, 15.0), (23.0, 15.0))

DEFAULT_SCENE = "hospital"
SCENES: dict[str, Scene] = {
    "hospital": Scene("hospital", DEFAULT_MAP, title="병원 복도", route=HOSPITAL_ROUTE),
    "hospital_busy": Scene("hospital_busy", DEFAULT_MAP, actors=HOSPITAL_BUSY_ACTORS, title="붐비는 병원",
                           route=HOSPITAL_ROUTE),
    "road": Scene("road", ROAD_MAP, outdoor=True, actors=ROAD_ACTORS, title="일상 도로",
                  route=((3.5, 8.5), (46.0, 8.5))),
    "plaza": Scene("plaza", PLAZA_MAP, outdoor=True, actors=PLAZA_ACTORS, title="광장",
                   route=((2.5, 9.5), (13.0, 9.5), (16.0, 7.5), (20.5, 7.5), (23.5, 9.5), (34.0, 9.5))),
    "alley": Scene("alley", ALLEY_MAP, outdoor=True, actors=ALLEY_ACTORS, title="주택가 골목",
                   route=((2.5, 6.5), (38.5, 6.5))),
    "station": Scene("station", STATION_MAP, actors=STATION_ACTORS, title="지하철역 대합실",
                     route=((2.5, 10.5), (6.0, 10.0), (42.0, 10.0))),
    "parking": Scene("parking", PARKING_MAP, outdoor=True, actors=PARKING_ACTORS, title="주차장",
                     route=((2.5, 11.5), (6.0, 12.5), (38.5, 12.5))),
    "park": Scene("park", PARK_MAP, outdoor=True, actors=PARK_ACTORS, title="공원",
                  route=((3.5, 10.5), (37.5, 10.5))),
}


def get_scene(name: str | None) -> Scene:
    """이름으로 장면을 고른다 (없거나 빈 이름이면 기본 장면)."""
    if not name:
        return SCENES[DEFAULT_SCENE]
    if name not in SCENES:
        raise ValueError(f"unknown scene '{name}' (choose from {', '.join(SCENES)})")
    return SCENES[name]
