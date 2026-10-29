/*
 * loopback_only.so — Webots가 '모든 주소'로 여는 포트를 이 PC(루프백)로만 열게 한다.
 *
 * Webots의 웹 서버(로봇 창·웹소켓, 기본 1234)는 받는 주소를 고르는 옵션이 없어 0.0.0.0 / [::]로 열린다 → 같은 네트워크의
 * 누구나 시뮬레이션을 조작할 수 있다 (KISA 설계 SR2-4 중요자원 접근통제). 조작은 체험·조작 포털(demo/server.py, 토큰 로그인)이
 * 127.0.0.1로 이어 주므로, Webots 자체는 이 PC 안에서만 열리면 된다.
 *
 * scripts/run_webots.sh가 LD_PRELOAD로 Webots에만 끼운다 (시스템 설정·방화벽은 건드리지 않는다).
 * bind()를 가로채, 주소가 '모든 주소'면 루프백으로 바꿔 진짜 bind()를 부른다:
 *   IPv4 0.0.0.0 → 127.0.0.1,   IPv6 [::] → [::ffff:127.0.0.1] (IPv4 루프백 접속은 그대로 받는다)
 * 그 밖의 주소(이미 루프백, 유닉스 소켓 등)는 손대지 않는다.
 *
 * 빌드: gcc -shared -fPIC -O2 -o loopback_only.so loopback_only.c -ldl   (run_webots.sh가 처음 한 번 만든다)
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dlfcn.h>
#include <netinet/in.h>
#include <string.h>
#include <sys/socket.h>

typedef int (*bind_fn)(int, const struct sockaddr *, socklen_t);

int bind(int fd, const struct sockaddr *addr, socklen_t len) {
  static bind_fn real_bind = NULL;
  if (!real_bind)
    real_bind = (bind_fn)dlsym(RTLD_NEXT, "bind");
  if (addr && addr->sa_family == AF_INET && len >= (socklen_t)sizeof(struct sockaddr_in)) {
    struct sockaddr_in in4;
    memcpy(&in4, addr, sizeof in4);
    if (in4.sin_addr.s_addr == htonl(INADDR_ANY)) {
      in4.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
      return real_bind(fd, (const struct sockaddr *)&in4, sizeof in4);
    }
  } else if (addr && addr->sa_family == AF_INET6 && len >= (socklen_t)sizeof(struct sockaddr_in6)) {
    struct sockaddr_in6 in6;
    memcpy(&in6, addr, sizeof in6);
    if (memcmp(&in6.sin6_addr, &in6addr_any, sizeof in6addr_any) == 0) {
      inet_pton(AF_INET6, "::ffff:127.0.0.1", &in6.sin6_addr);
      return real_bind(fd, (const struct sockaddr *)&in6, sizeof in6);
    }
  }
  return real_bind(fd, addr, len);
}
