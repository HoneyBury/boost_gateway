// BoostGateway C# SDK v3.0.0
// Unity-compatible client for BoostGateway game servers.
// Supports: connect, login, room operations, battle input, callbacks.

using System;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace BoostGateway.Sdk
{
    public struct LoginResult
    {
        public bool Ok;
        public string UserId;
        public int ErrorCode;
    }

    public struct RoomResult
    {
        public bool Ok;
        public string RoomId;
        public int ErrorCode;
    }

    public struct PushMessage
    {
        public ushort MessageId;
        public string Body;
    }

    public class SdkClient
    {
        private TcpClient _client;
        private NetworkStream _stream;
        private uint _reqId;
        private Action<PushMessage> _pushCallback;

        public bool Connect(string host = "127.0.0.1", int port = 9201, float timeout = 5f)
        {
            try
            {
                _client = new TcpClient();
                _client.Connect(host, port);
                _stream = _client.GetStream();
                _stream.ReadTimeout = (int)(timeout * 1000);
                return true;
            }
            catch { return false; }
        }

        public void Disconnect()
        {
            _stream?.Close();
            _client?.Close();
        }

        public LoginResult Login(string userId, string token)
        {
            var body = $"{userId}|token:{token}|{userId}";
            var resp = Request(2001, body);
            if (resp.HasValue && resp.Value.msgId == 2002)
                return new LoginResult { Ok = true, UserId = userId };
            return new LoginResult { Ok = false, ErrorCode = -1 };
        }

        public RoomResult CreateRoom(string roomId)
        {
            var resp = Request(3001, roomId);
            return new RoomResult { Ok = resp.HasValue && resp.Value.msgId == 3002, RoomId = roomId };
        }

        public RoomResult JoinRoom(string roomId)
        {
            var resp = Request(3003, roomId);
            return new RoomResult { Ok = resp.HasValue && resp.Value.msgId == 3004, RoomId = roomId };
        }

        public RoomResult LeaveRoom(string roomId)
        {
            var resp = Request(3005, roomId);
            return new RoomResult { Ok = resp.HasValue && resp.Value.msgId == 3006, RoomId = roomId };
        }

        public RoomResult SetReady(bool ready = true)
        {
            var resp = Request(3007, ready ? "true" : "false");
            return new RoomResult { Ok = resp.HasValue && resp.Value.msgId == 3008 };
        }

        public bool StartBattle(string roomId) =>
            Request(4001, roomId).HasValue;

        public bool SendInput(string inputData) =>
            Request(4003, inputData).HasValue;

        public void OnPush(Action<PushMessage> callback) => _pushCallback = callback;

        private (ushort msgId, uint reqId, int errCode, byte flags, string body)?
            Request(ushort msgId, string body)
        {
            if (_stream == null) return null;
            try
            {
                _reqId++;
                var data = Encode(msgId, _reqId, 0, body);
                _stream.Write(data, 0, data.Length);
                return Read();
            }
            catch { return null; }
        }

        private static byte[] Encode(ushort msgId, uint reqId, int errCode, string body)
        {
            var bodyBytes = Encoding.UTF8.GetBytes(body);
            var total = 11 + bodyBytes.Length;
            var result = new byte[4 + total];
            result[0] = (byte)(total >> 24);
            result[1] = (byte)(total >> 16);
            result[2] = (byte)(total >> 8);
            result[3] = (byte)total;
            result[4] = (byte)(msgId >> 8);
            result[5] = (byte)msgId;
            result[6] = (byte)(reqId >> 24);
            result[7] = (byte)(reqId >> 16);
            result[8] = (byte)(reqId >> 8);
            result[9] = (byte)reqId;
            var ec = (uint)errCode;
            result[10] = (byte)(ec >> 24);
            result[11] = (byte)(ec >> 16);
            result[12] = (byte)(ec >> 8);
            result[13] = (byte)ec;
            result[14] = 0; // flags
            Buffer.BlockCopy(bodyBytes, 0, result, 15, bodyBytes.Length);
            return result;
        }

        private (ushort, uint, int, byte, string)? Read()
        {
            var lenBuf = new byte[4];
            if (_stream.Read(lenBuf, 0, 4) < 4) return null;
            var total = (uint)(lenBuf[0] << 24 | lenBuf[1] << 16 | lenBuf[2] << 8 | lenBuf[3]);
            var remaining = (int)total - 4;
            var data = new byte[remaining];
            var offset = 0;
            while (offset < remaining)
            {
                var n = _stream.Read(data, offset, remaining - offset);
                if (n <= 0) break;
                offset += n;
            }
            var msgId = (ushort)(data[0] << 8 | data[1]);
            var reqId = (uint)(data[2] << 24 | data[3] << 16 | data[4] << 8 | data[5]);
            var ec = (int)((uint)(data[6] << 24 | data[7] << 16 | data[8] << 8 | data[9]));
            var flags = data[10];
            var body = Encoding.UTF8.GetString(data, 11, data.Length - 11);
            return (msgId, reqId, ec, flags, body);
        }
    }
}
