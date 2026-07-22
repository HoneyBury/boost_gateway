// SDK v4.2.0: Thin C# wrapper via C API (DllImport, zero deps).
using System;
using System.Runtime.InteropServices;

namespace BoostGateway.Sdk
{
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
    public struct LoginResult { public int Ok; public int ErrorCode;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=64)] public string UserId;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=64)] public string DisplayName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)] public string ErrorMessage; }

    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
    public struct RoomResult { public int Ok; public int ErrorCode;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=64)] public string RoomId;
        public int MemberCount;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)] public string ErrorMessage; }

    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
    public struct MatchResult { public int Ok; public int ErrorCode;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=4096)] public string ResponseBody;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)] public string ErrorMessage; }

    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
    public struct LeaderboardSubmitResult { public int Ok; public int ErrorCode;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=4096)] public string ResponseBody;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)] public string ErrorMessage; }

    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
    public struct LeaderboardQueryResult { public int Ok; public int ErrorCode;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=4096)] public string ResponseBody;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)] public string ErrorMessage; }

    public class SdkClient : IDisposable
    {
        public const string ExpectedVersion = "4.2.0";
        IntPtr _h;
        [DllImport("boost_gateway_sdk")] static extern IntPtr gsdk_version();
        [DllImport("boost_gateway_sdk")] static extern IntPtr gsdk_create();
        [DllImport("boost_gateway_sdk")] static extern void gsdk_destroy(IntPtr h);
        [DllImport("boost_gateway_sdk")] static extern int gsdk_connect(IntPtr h, string host, ushort port, int ms);
        [DllImport("boost_gateway_sdk")] static extern void gsdk_disconnect(IntPtr h);
        [DllImport("boost_gateway_sdk")] static extern void gsdk_start_heartbeat(IntPtr h, int seconds);
        [DllImport("boost_gateway_sdk")] static extern void gsdk_stop_heartbeat(IntPtr h);
        [DllImport("boost_gateway_sdk")] static extern LoginResult gsdk_login(IntPtr h, string uid, string token, int ms);
        [DllImport("boost_gateway_sdk")] static extern RoomResult gsdk_create_room(IntPtr h, string rid, int ms);
        [DllImport("boost_gateway_sdk")] static extern RoomResult gsdk_join_room(IntPtr h, string rid, int ms);
        [DllImport("boost_gateway_sdk")] static extern RoomResult gsdk_leave_room(IntPtr h, string rid, int ms);
        [DllImport("boost_gateway_sdk")] static extern RoomResult gsdk_set_ready(IntPtr h, int ready, int ms);
        [DllImport("boost_gateway_sdk")] static extern BattleStartResult gsdk_start_battle(IntPtr h, string roomId, int ms);
        [DllImport("boost_gateway_sdk")] static extern BattleInputResult gsdk_send_battle_input(IntPtr h, string input, int ms);
        [DllImport("boost_gateway_sdk")] static extern MatchResult gsdk_match_join(IntPtr h, string userId, long mmr, string mode, int ms);
        [DllImport("boost_gateway_sdk")] static extern MatchResult gsdk_match_leave(IntPtr h, string userId, string mode, int ms);
        [DllImport("boost_gateway_sdk")] static extern MatchResult gsdk_match_status(IntPtr h, string userId, string mode, int ms);
        [DllImport("boost_gateway_sdk")] static extern LeaderboardSubmitResult gsdk_leaderboard_submit(IntPtr h, string userId, string displayName, long score, int ms);
        [DllImport("boost_gateway_sdk")] static extern LeaderboardQueryResult gsdk_leaderboard_top(IntPtr h, uint k, int ms);
        [DllImport("boost_gateway_sdk")] static extern LeaderboardQueryResult gsdk_leaderboard_rank(IntPtr h, string userId, int ms);
        [DllImport("boost_gateway_sdk")] static extern EchoResult gsdk_echo(IntPtr h, string body, int ms);

        public SdkClient()
        {
            AssertCompatibleNativeVersion();
            _h = gsdk_create();
            if (_h == IntPtr.Zero) throw new InvalidOperationException("BoostGateway SDK native client allocation failed");
        }
        public void Dispose() { if (_h != IntPtr.Zero) { gsdk_destroy(_h); _h = IntPtr.Zero; } }
        public static string Version => Marshal.PtrToStringAnsi(gsdk_version()) ?? "";
        public static void AssertCompatibleNativeVersion()
        {
            var version = Version;
            if (!String.Equals(version, ExpectedVersion, StringComparison.Ordinal))
                throw new InvalidOperationException($"BoostGateway SDK native version mismatch: expected {ExpectedVersion}, got {version}");
        }
        public bool Connect(string h="127.0.0.1", ushort p=9201, int ms=5000) => gsdk_connect(_h, h, p, ms) != 0;
        public void Disconnect() => gsdk_disconnect(_h);
        public void StartHeartbeat(int seconds=15) => gsdk_start_heartbeat(_h, seconds);
        public void StopHeartbeat() => gsdk_stop_heartbeat(_h);
        public LoginResult Login(string u, string t, int ms=5000) => gsdk_login(_h, u, t, ms);
        public RoomResult CreateRoom(string r, int ms=5000) => gsdk_create_room(_h, r, ms);
        public RoomResult JoinRoom(string r, int ms=5000) => gsdk_join_room(_h, r, ms);
        public RoomResult LeaveRoom(string r, int ms=5000) => gsdk_leave_room(_h, r, ms);
        public RoomResult SetReady(bool r=true, int ms=5000) => gsdk_set_ready(_h, r?1:0, ms);
        public BattleStartResult StartBattle(string r, int ms=5000) => gsdk_start_battle(_h, r, ms);
        public BattleInputResult SendBattleInput(string input, int ms=5000) => gsdk_send_battle_input(_h, input, ms);
        public MatchResult MatchJoin(string userId, long mmr=1000, string mode="1v1", int ms=5000) => gsdk_match_join(_h, userId, mmr, mode, ms);
        public MatchResult MatchLeave(string userId, string mode="1v1", int ms=5000) => gsdk_match_leave(_h, userId, mode, ms);
        public MatchResult MatchStatus(string userId, string mode="1v1", int ms=5000) => gsdk_match_status(_h, userId, mode, ms);
        public LeaderboardSubmitResult LeaderboardSubmit(string userId, string displayName, long score, int ms=5000) => gsdk_leaderboard_submit(_h, userId, displayName, score, ms);
        public LeaderboardQueryResult LeaderboardTop(uint k=10, int ms=5000) => gsdk_leaderboard_top(_h, k, ms);
        public LeaderboardQueryResult LeaderboardRank(string userId, int ms=5000) => gsdk_leaderboard_rank(_h, userId, ms);
        public EchoResult Echo(string body, int ms=5000) => gsdk_echo(_h, body, ms);
    }

    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
    public struct BattleStartResult { public int Ok; public int ErrorCode;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=64)] public string BattleId;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)] public string ErrorMessage; }

    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
    public struct BattleInputResult { public int Ok; public int ErrorCode; public ulong InputSeq;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)] public string ErrorMessage; }

    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
    public struct EchoResult { public int Ok;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=4096)] public string Body; }
}
