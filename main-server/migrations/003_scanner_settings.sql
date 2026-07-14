-- Scanner feature flags (admin-togglable)
INSERT INTO system_messages(key, value, description) VALUES
  ('scanner_npv_to_v2ray', 'true', 'تبدیل محلی NPV/ساب به v2ray'),
  ('scanner_decrypt_bot', 'true', 'استفاده از @VPNDecryptorBot'),
  ('scanner_protocol_vless', 'true', 'اسکن پروتکل VLESS'),
  ('scanner_protocol_vmess', 'true', 'اسکن پروتکل VMess'),
  ('scanner_protocol_trojan', 'true', 'اسکن پروتکل Trojan'),
  ('scanner_protocol_ss', 'true', 'اسکن پروتکل Shadowsocks'),
  ('scanner_protocol_wireguard', 'false', 'اسکن پروتکل WireGuard')
ON CONFLICT (key) DO NOTHING;
