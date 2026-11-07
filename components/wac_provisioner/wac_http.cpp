#include "wac_http.h"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

namespace esphome::wac_provisioner::http {

namespace {

bool wait_fd(int fd, bool for_write, int timeout_ms) {
  fd_set set;
  FD_ZERO(&set);
  FD_SET(fd, &set);
  timeval tv{timeout_ms / 1000, (timeout_ms % 1000) * 1000};
  int r = select(fd + 1, for_write ? nullptr : &set, for_write ? &set : nullptr, nullptr, &tv);
  return r > 0;
}

bool send_all(int fd, const uint8_t *data, size_t len, int timeout_ms) {
  while (len > 0) {
    if (!wait_fd(fd, true, timeout_ms))
      return false;
    ssize_t n = send(fd, data, len, 0);
    if (n <= 0)
      return false;
    data += n;
    len -= static_cast<size_t>(n);
  }
  return true;
}

class Socket {
 public:
  explicit Socket(int fd) : fd_(fd) {}
  ~Socket() {
    if (this->fd_ >= 0)
      close(this->fd_);
  }
  int get() const { return this->fd_; }

 private:
  int fd_;
};

}  // namespace

bool post(const char *ipv4, uint16_t port, const char *path, const protocol::Bytes &body, int timeout_ms,
          size_t max_body, Response &out) {
  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(port);
  if (inet_pton(AF_INET, ipv4, &addr.sin_addr) != 1)
    return false;

  Socket sock(socket(AF_INET, SOCK_STREAM, 0));
  int fd = sock.get();
  if (fd < 0)
    return false;
  int flags = fcntl(fd, F_GETFL, 0);
  fcntl(fd, F_SETFL, flags | O_NONBLOCK);
  if (connect(fd, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) != 0) {
    if (errno != EINPROGRESS || !wait_fd(fd, true, timeout_ms))
      return false;
    int err = 0;
    socklen_t len = sizeof(err);
    if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len) != 0 || err != 0)
      return false;
  }

  char head[192];
  int n = snprintf(head, sizeof(head),
                   "POST /%s HTTP/1.1\r\nHost: %s\r\nContent-Type: application/octet-stream\r\n"
                   "Connection: close\r\nContent-Length: %u\r\n\r\n",
                   path, ipv4, static_cast<unsigned>(body.size()));
  if (n <= 0 || static_cast<size_t>(n) >= sizeof(head))
    return false;
  if (!send_all(fd, reinterpret_cast<const uint8_t *>(head), static_cast<size_t>(n), timeout_ms) ||
      !send_all(fd, body.data(), body.size(), timeout_ms))
    return false;

  // Read until the server closes (Connection: close) or Content-Length is satisfied.
  const size_t max_total = max_body + 1024;
  std::string raw;
  size_t header_end = std::string::npos;
  long content_length = -1;
  char buf[512];
  while (true) {
    if (!wait_fd(fd, false, timeout_ms))
      return false;
    ssize_t got = recv(fd, buf, sizeof(buf), 0);
    if (got < 0)
      return false;
    if (got == 0)
      break;
    raw.append(buf, static_cast<size_t>(got));
    if (raw.size() > max_total)
      return false;
    if (header_end == std::string::npos) {
      header_end = raw.find("\r\n\r\n");
      if (header_end != std::string::npos) {
        for (size_t pos = raw.find("\r\n") + 2; pos < header_end;) {
          size_t eol = raw.find("\r\n", pos);
          std::string line = raw.substr(pos, eol - pos);
          if (line.size() > 15 && strncasecmp(line.c_str(), "Content-Length:", 15) == 0)
            content_length = strtol(line.c_str() + 15, nullptr, 10);
          pos = eol + 2;
        }
      }
    }
    if (header_end != std::string::npos && content_length >= 0 &&
        raw.size() - (header_end + 4) >= static_cast<size_t>(content_length))
      break;
  }
  if (header_end == std::string::npos || sscanf(raw.c_str(), "HTTP/1.%*d %d", &out.status) != 1)
    return false;
  size_t start = header_end + 4;
  size_t len = raw.size() - start;
  if (content_length >= 0)
    len = std::min(len, static_cast<size_t>(content_length));
  if (len > max_body)
    return false;
  out.body.assign(raw.begin() + start, raw.begin() + start + len);
  return true;
}

}  // namespace esphome::wac_provisioner::http
