"""Small runtime i18n helper for backend-facing user messages."""

from __future__ import annotations

from typing import Any


def _parse_language(language: str | None) -> str:
    raw = (language or "en").strip().lower()
    if raw.startswith("zh") or raw in {"cn", "chinese"}:
        return "zh"
    if raw.startswith("fr") or raw in {"french", "francais", "français"}:
        return "fr"
    if raw.startswith(("ja", "jp")) or raw in {"japanese", "日本語"}:
        return "ja"
    if raw.startswith(("ko", "kr")) or raw in {"korean", "한국어"}:
        return "ko"
    base = raw.replace("_", "-").split("-", 1)[0]
    if base == "vi" or raw in {"vietnamese", "tiếng việt", "tieng viet"}:
        return "vi"
    if base == "tr" or raw in {"turkish", "türkçe", "turkce"}:
        return "tr"
    if base == "pt" or raw in {"portuguese", "português", "portugues"}:
        return "pt"
    if base == "it" or raw in {"italian", "italiano"}:
        return "it"
    if base == "hu" or raw in {"hungarian", "magyar"}:
        return "hu"
    return "en"


_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "api.content_required": "content is required",
        "api.invalid_channels_config": "Invalid channels config",
        "api.partner_already_exists": "Partner '{name}' already exists",
        "api.partner_not_found": "Partner not found",
        "api.partner_not_found_or_not_running": "Partner not found or not running",
        "api.partner_not_running": "Partner not running",
        "api.partner_stopped_start_required": "Partner is stopped. Start it before chatting.",
        "api.persona_already_exists": "Persona already exists: {name}",
        "api.persona_name_required": "Persona name is required",
        "api.persona_not_found": "Persona not found: {name}",
        "api.soul_already_exists": "Soul '{name}' already exists",
        "api.soul_content_empty": "Custom soul content is empty",
        "api.soul_library_not_found": "Soul '{name}' not found in library",
        "api.soul_not_found": "Soul not found",
        "api.tool_not_found": "Tool '{name}' not found",
        "cli_apps.abi_mismatch": (
            "The CLI app {app!r} was installed for {installed} but this runtime is "
            "{current}. An administrator needs to reinstall it."
        ),
        "cli_apps.args_required": (
            "{tool} needs an 'args' array — one command-line argument per element."
        ),
        "cli_apps.entry_admin_only": (
            "CLI apps are installed by an administrator; ask yours to add this one."
        ),
        "cli_apps.install_in_progress": "That app is already being installed.",
        "cli_apps.not_in_catalog": "No CLI app named {id!r} in the catalog.",
        "cli_apps.not_installed": (
            "The CLI app {app!r} is not installed on this deployment any more."
        ),
        "cli_apps.still_running": "{app} is still running ({seconds}s)",
        "mcp.configure_command_or_url": "Server {name!r}: configure either a command (stdio) or a url.",
        "mcp.configure_before_testing": "Configure either a command (stdio) or a url before testing.",
        "mcp.server_error": "Server {name!r}: {error}",
        "mcp.server_missing": "No server named {name!r} in your list.",
        "mcp.not_oauth": "This server does not use OAuth; give it a credential instead.",
        "mcp.oauth_callback_incomplete": "The authorization response was incomplete.",
        "mcp.oauth_callback_unknown": "That authorization has expired or already completed. Start it again.",
        "mcp.oauth_done": "Authorized. You can close this tab.",
        "mcp.oauth_failed": "Authorization failed.",
        "mcp.catalog_entry_missing": "No MCP service named {id!r} in the catalog.",
        "mcp.entry_admin_only": (
            "This service runs as a local command and can only be added by an administrator."
        ),
        "mcp.tool_not_available": (
            "This tool is not available in this conversation. "
            "Only the tools listed in the prompt can be called."
        ),
        "sandbox.command_blocked": "Error: command blocked by safety guard (dangerous pattern).",
        "sandbox.disabled_for_account": "Code execution is disabled for your account.",
        "sandbox.no_backend": "no sandbox backend available",
    },
    "zh": {
        "api.content_required": "content 不能为空",
        "api.invalid_channels_config": "渠道配置无效",
        "api.partner_already_exists": "伙伴 '{name}' 已存在",
        "api.partner_not_found": "未找到伙伴",
        "api.partner_not_found_or_not_running": "未找到伙伴或伙伴未运行",
        "api.partner_not_running": "伙伴未运行",
        "api.partner_stopped_start_required": "伙伴已停止。请先启动后再聊天。",
        "api.persona_already_exists": "Persona 已存在：{name}",
        "api.persona_name_required": "Persona 名称不能为空",
        "api.persona_not_found": "未找到 Persona：{name}",
        "api.soul_already_exists": "Soul '{name}' 已存在",
        "api.soul_content_empty": "自定义 soul 内容为空",
        "api.soul_library_not_found": "素材库中未找到 soul '{name}'",
        "api.soul_not_found": "未找到 soul",
        "api.tool_not_found": "未找到工具 '{name}'",
        "cli_apps.abi_mismatch": (
            "CLI 应用 {app!r} 是为 {installed} 安装的，当前运行环境是 {current}，需要管理员重新安装。"
        ),
        "cli_apps.args_required": "{tool} 需要 args 数组：每个命令行参数占一个元素。",
        "cli_apps.entry_admin_only": "CLI 应用由管理员安装，请联系管理员添加。",
        "cli_apps.install_in_progress": "该应用正在安装中。",
        "cli_apps.not_in_catalog": "目录中没有名为 {id!r} 的 CLI 应用。",
        "cli_apps.not_installed": "CLI 应用 {app!r} 已不在本部署中。",
        "cli_apps.still_running": "{app} 仍在运行（已 {seconds} 秒）",
        "mcp.configure_command_or_url": "服务器 {name!r}：请配置 command（stdio）或 url。",
        "mcp.configure_before_testing": "测试前请先配置 command（stdio）或 url。",
        "mcp.server_error": "服务器 {name!r}：{error}",
        "mcp.server_missing": "你的列表中没有名为 {name!r} 的服务器。",
        "mcp.not_oauth": "该服务器不使用 OAuth，请改为填写凭据。",
        "mcp.oauth_callback_incomplete": "授权回调信息不完整。",
        "mcp.oauth_callback_unknown": "该授权已过期或已完成，请重新发起。",
        "mcp.oauth_done": "已授权，可以关闭此页。",
        "mcp.oauth_failed": "授权失败。",
        "mcp.catalog_entry_missing": "目录中没有名为 {id!r} 的 MCP 服务。",
        "mcp.entry_admin_only": "该服务以本地命令方式运行，只能由管理员添加。",
        "mcp.tool_not_available": "该工具在本次对话中不可用，只能调用提示中列出的工具。",
        "sandbox.command_blocked": "错误：命令被安全防护拦截（匹配危险模式）。",
        "sandbox.disabled_for_account": "你的账号已禁用代码执行。",
        "sandbox.no_backend": "没有可用的沙箱后端",
    },
    "fr": {
        "api.content_required": "le contenu est requis",
        "api.invalid_channels_config": "Configuration des canaux invalide",
        "api.partner_already_exists": "Le partenaire « {name} » existe déjà",
        "api.partner_not_found": "Partenaire introuvable",
        "api.partner_not_found_or_not_running": "Partenaire introuvable ou non démarré",
        "api.partner_not_running": "Le partenaire n’est pas démarré",
        "api.partner_stopped_start_required": (
            "Le partenaire est arrêté. Démarrez-le avant de discuter."
        ),
        "api.persona_already_exists": "Le persona existe déjà : {name}",
        "api.persona_name_required": "Le nom du persona est requis",
        "api.persona_not_found": "Persona introuvable : {name}",
        "api.soul_already_exists": "Le soul « {name} » existe déjà",
        "api.soul_content_empty": "Le contenu personnalisé du soul est vide",
        "api.soul_library_not_found": "Soul « {name} » introuvable dans la bibliothèque",
        "api.soul_not_found": "Soul introuvable",
        "api.tool_not_found": "Outil « {name} » introuvable",
        "cli_apps.abi_mismatch": (
            "L’application CLI {app!r} a été installée pour {installed}, mais ce runtime "
            "est {current}. Un administrateur doit la réinstaller."
        ),
        "cli_apps.args_required": (
            "{tool} nécessite un tableau « args » — un argument de ligne de commande par élément."
        ),
        "cli_apps.entry_admin_only": (
            "Les applications CLI sont installées par un administrateur ; "
            "demandez-lui d’ajouter celle-ci."
        ),
        "cli_apps.install_in_progress": "Cette application est déjà en cours d’installation.",
        "cli_apps.not_in_catalog": "Aucune application CLI nommée {id!r} dans le catalogue.",
        "cli_apps.not_installed": (
            "L’application CLI {app!r} n’est plus installée sur ce déploiement."
        ),
        "cli_apps.still_running": "{app} est toujours en cours d’exécution ({seconds} s)",
        "mcp.configure_command_or_url": (
            "Serveur {name!r} : configurez soit une command (stdio), soit une url."
        ),
        "mcp.configure_before_testing": (
            "Configurez une command (stdio) ou une url avant de tester."
        ),
        "mcp.server_error": "Serveur {name!r} : {error}",
        "mcp.server_missing": "Aucun serveur nommé {name!r} dans votre liste.",
        "mcp.not_oauth": ("Ce serveur n’utilise pas OAuth ; fournissez-lui plutôt un identifiant."),
        "mcp.oauth_callback_incomplete": "La réponse d’autorisation est incomplète.",
        "mcp.oauth_callback_unknown": (
            "Cette autorisation a expiré ou est déjà terminée. Recommencez."
        ),
        "mcp.oauth_done": "Autorisé. Vous pouvez fermer cet onglet.",
        "mcp.oauth_failed": "L’autorisation a échoué.",
        "mcp.catalog_entry_missing": "Aucun service MCP nommé {id!r} dans le catalogue.",
        "mcp.entry_admin_only": (
            "Ce service s’exécute comme commande locale et ne peut être ajouté que "
            "par un administrateur."
        ),
        "mcp.tool_not_available": (
            "Cet outil n’est pas disponible dans cette conversation. Seuls les outils "
            "listés dans l’invite peuvent être appelés."
        ),
        "sandbox.command_blocked": (
            "Erreur : commande bloquée par le garde-fou de sécurité (motif dangereux)."
        ),
        "sandbox.disabled_for_account": "L’exécution de code est désactivée pour votre compte.",
        "sandbox.no_backend": "aucun backend de bac à sable disponible",
    },
    "ja": {
        "api.content_required": "content は必須です",
        "api.invalid_channels_config": "チャンネル設定が無効です",
        "api.partner_already_exists": "パートナー '{name}' はすでに存在します",
        "api.partner_not_found": "パートナーが見つかりません",
        "api.partner_not_found_or_not_running": "パートナーが見つからないか、実行されていません",
        "api.partner_not_running": "パートナーは実行されていません",
        "api.partner_stopped_start_required": "パートナーは停止しています。チャットする前に起動してください。",
        "api.persona_already_exists": "Persona はすでに存在します: {name}",
        "api.persona_name_required": "Persona 名は必須です",
        "api.persona_not_found": "Persona が見つかりません: {name}",
        "api.soul_already_exists": "Soul '{name}' はすでに存在します",
        "api.soul_content_empty": "カスタム soul の内容が空です",
        "api.soul_library_not_found": "ライブラリに soul '{name}' が見つかりません",
        "api.soul_not_found": "Soul が見つかりません",
        "api.tool_not_found": "ツール '{name}' が見つかりません",
        "cli_apps.abi_mismatch": (
            "CLI アプリ {app!r} は {installed} 向けにインストールされていますが、"
            "現在のランタイムは {current} です。管理者による再インストールが必要です。"
        ),
        "cli_apps.args_required": (
            "{tool} には 'args' 配列が必要です。コマンドライン引数を 1 要素につき 1 つ指定してください。"
        ),
        "cli_apps.entry_admin_only": (
            "CLI アプリは管理者がインストールします。追加は管理者に依頼してください。"
        ),
        "cli_apps.install_in_progress": "このアプリはすでにインストール中です。",
        "cli_apps.not_in_catalog": "カタログに {id!r} という名前の CLI アプリはありません。",
        "cli_apps.not_installed": "CLI アプリ {app!r} はこのデプロイにはもうインストールされていません。",
        "cli_apps.still_running": "{app} はまだ実行中です（{seconds} 秒）",
        "mcp.configure_command_or_url": (
            "サーバー {name!r}: command（stdio）または url のいずれかを設定してください。"
        ),
        "mcp.configure_before_testing": (
            "テストの前に command（stdio）または url のいずれかを設定してください。"
        ),
        "mcp.server_error": "サーバー {name!r}: {error}",
        "mcp.server_missing": "リストに {name!r} という名前のサーバーはありません。",
        "mcp.not_oauth": "このサーバーは OAuth を使用しません。代わりに認証情報を指定してください。",
        "mcp.oauth_callback_incomplete": "認可レスポンスが不完全です。",
        "mcp.oauth_callback_unknown": "この認可は期限切れか、すでに完了しています。もう一度開始してください。",
        "mcp.oauth_done": "認可されました。このタブを閉じてかまいません。",
        "mcp.oauth_failed": "認可に失敗しました。",
        "mcp.catalog_entry_missing": "カタログに {id!r} という名前の MCP サービスはありません。",
        "mcp.entry_admin_only": (
            "このサービスはローカルコマンドとして実行されるため、管理者のみが追加できます。"
        ),
        "mcp.tool_not_available": (
            "このツールはこの会話では使用できません。"
            "プロンプトに記載されたツールのみ呼び出せます。"
        ),
        "sandbox.command_blocked": "エラー: コマンドは安全ガードによってブロックされました（危険なパターン）。",
        "sandbox.disabled_for_account": "お使いのアカウントではコード実行が無効になっています。",
        "sandbox.no_backend": "利用可能なサンドボックスバックエンドがありません",
    },
    "ko": {
        "api.content_required": "content는 필수입니다",
        "api.invalid_channels_config": "채널 설정이 잘못되었습니다",
        "api.partner_already_exists": "파트너 '{name}'이(가) 이미 존재합니다",
        "api.partner_not_found": "파트너를 찾을 수 없습니다",
        "api.partner_not_found_or_not_running": "파트너를 찾을 수 없거나 실행 중이 아닙니다",
        "api.partner_not_running": "파트너가 실행 중이 아닙니다",
        "api.partner_stopped_start_required": "파트너가 중지되었습니다. 채팅하기 전에 시작하세요.",
        "api.persona_already_exists": "Persona가 이미 존재합니다: {name}",
        "api.persona_name_required": "Persona 이름은 필수입니다",
        "api.persona_not_found": "Persona를 찾을 수 없습니다: {name}",
        "api.soul_already_exists": "Soul '{name}'이(가) 이미 존재합니다",
        "api.soul_content_empty": "사용자 지정 soul 내용이 비어 있습니다",
        "api.soul_library_not_found": "라이브러리에서 soul '{name}'을(를) 찾을 수 없습니다",
        "api.soul_not_found": "Soul을 찾을 수 없습니다",
        "api.tool_not_found": "도구 '{name}'을(를) 찾을 수 없습니다",
        "cli_apps.abi_mismatch": (
            "CLI 앱 {app!r}은(는) {installed}용으로 설치되었지만 현재 런타임은 "
            "{current}입니다. 관리자가 다시 설치해야 합니다."
        ),
        "cli_apps.args_required": (
            "{tool}에는 'args' 배열이 필요합니다. 요소마다 명령줄 인수를 하나씩 지정하세요."
        ),
        "cli_apps.entry_admin_only": (
            "CLI 앱은 관리자가 설치합니다. 관리자에게 이 앱을 추가해 달라고 요청하세요."
        ),
        "cli_apps.install_in_progress": "이 앱은 이미 설치 중입니다.",
        "cli_apps.not_in_catalog": "카탈로그에 {id!r}(이)라는 CLI 앱이 없습니다.",
        "cli_apps.not_installed": "CLI 앱 {app!r}은(는) 더 이상 이 배포에 설치되어 있지 않습니다.",
        "cli_apps.still_running": "{app}이(가) 아직 실행 중입니다({seconds}초)",
        "mcp.configure_command_or_url": (
            "서버 {name!r}: command(stdio) 또는 url 중 하나를 설정하세요."
        ),
        "mcp.configure_before_testing": (
            "테스트하기 전에 command(stdio) 또는 url 중 하나를 설정하세요."
        ),
        "mcp.server_error": "서버 {name!r}: {error}",
        "mcp.server_missing": "목록에 {name!r}(이)라는 서버가 없습니다.",
        "mcp.not_oauth": "이 서버는 OAuth를 사용하지 않습니다. 대신 자격 증명을 입력하세요.",
        "mcp.oauth_callback_incomplete": "인증 응답이 불완전합니다.",
        "mcp.oauth_callback_unknown": "이 인증은 만료되었거나 이미 완료되었습니다. 다시 시작하세요.",
        "mcp.oauth_done": "인증되었습니다. 이 탭을 닫아도 됩니다.",
        "mcp.oauth_failed": "인증에 실패했습니다.",
        "mcp.catalog_entry_missing": "카탈로그에 {id!r}(이)라는 MCP 서비스가 없습니다.",
        "mcp.entry_admin_only": (
            "이 서비스는 로컬 명령으로 실행되므로 관리자만 추가할 수 있습니다."
        ),
        "mcp.tool_not_available": (
            "이 도구는 이 대화에서 사용할 수 없습니다. "
            "프롬프트에 나열된 도구만 호출할 수 있습니다."
        ),
        "sandbox.command_blocked": "오류: 안전 장치가 명령을 차단했습니다(위험한 패턴).",
        "sandbox.disabled_for_account": "계정에서 코드 실행이 비활성화되어 있습니다.",
        "sandbox.no_backend": "사용 가능한 샌드박스 백엔드가 없습니다",
    },
    "vi": {
        "api.content_required": "content là bắt buộc",
        "api.invalid_channels_config": "Cấu hình kênh không hợp lệ",
        "api.partner_already_exists": "Bạn đồng hành '{name}' đã tồn tại",
        "api.partner_not_found": "Không tìm thấy bạn đồng hành",
        "api.partner_not_found_or_not_running": "Không tìm thấy bạn đồng hành hoặc bạn đồng hành không chạy",
        "api.partner_not_running": "Bạn đồng hành không chạy",
        "api.partner_stopped_start_required": "Bạn đồng hành đã dừng. Hãy khởi động trước khi trò chuyện.",
        "api.persona_already_exists": "Persona đã tồn tại: {name}",
        "api.persona_name_required": "Bắt buộc nhập tên persona",
        "api.persona_not_found": "Không tìm thấy persona: {name}",
        "api.soul_already_exists": "Soul '{name}' đã tồn tại",
        "api.soul_content_empty": "Nội dung Soul tùy chỉnh đang trống",
        "api.soul_library_not_found": "Không tìm thấy Soul '{name}' trong thư viện",
        "api.soul_not_found": "Không tìm thấy Soul",
        "api.tool_not_found": "Không tìm thấy công cụ '{name}'",
        "cli_apps.abi_mismatch": (
            "Ứng dụng CLI {app!r} được cài cho {installed}, nhưng môi trường chạy hiện tại là "
            "{current}. Quản trị viên cần cài lại ứng dụng này."
        ),
        "cli_apps.args_required": "{tool} cần một mảng 'args' — mỗi phần tử là một đối số dòng lệnh.",
        "cli_apps.entry_admin_only": "Ứng dụng CLI do quản trị viên cài đặt; hãy nhờ quản trị viên thêm ứng dụng này.",
        "cli_apps.install_in_progress": "Ứng dụng này đang được cài đặt.",
        "cli_apps.not_in_catalog": "Danh mục không có ứng dụng CLI nào tên {id!r}.",
        "cli_apps.not_installed": "Ứng dụng CLI {app!r} không còn được cài trên hệ thống này.",
        "cli_apps.still_running": "{app} vẫn đang chạy ({seconds} giây)",
        "mcp.configure_command_or_url": "Máy chủ {name!r}: hãy cấu hình một lệnh (stdio) hoặc một URL.",
        "mcp.configure_before_testing": "Hãy cấu hình một lệnh (stdio) hoặc một URL trước khi kiểm tra.",
        "mcp.server_error": "Máy chủ {name!r}: {error}",
        "mcp.server_missing": "Danh sách của bạn không có máy chủ nào tên {name!r}.",
        "mcp.not_oauth": "Máy chủ này không dùng OAuth; hãy cung cấp thông tin xác thực thay thế.",
        "mcp.oauth_callback_incomplete": "Phản hồi ủy quyền không đầy đủ.",
        "mcp.oauth_callback_unknown": "Lượt ủy quyền này đã hết hạn hoặc đã hoàn tất. Hãy bắt đầu lại.",
        "mcp.oauth_done": "Đã ủy quyền. Bạn có thể đóng thẻ này.",
        "mcp.oauth_failed": "Ủy quyền thất bại.",
        "mcp.catalog_entry_missing": "Danh mục không có dịch vụ MCP nào tên {id!r}.",
        "mcp.entry_admin_only": "Dịch vụ này chạy dưới dạng lệnh cục bộ nên chỉ quản trị viên mới có thể thêm.",
        "mcp.tool_not_available": (
            "Công cụ này không khả dụng trong cuộc trò chuyện này. Chỉ có thể gọi các công cụ được "
            "liệt kê trong prompt."
        ),
        "sandbox.command_blocked": "Lỗi: lệnh bị cơ chế an toàn chặn (mẫu nguy hiểm).",
        "sandbox.disabled_for_account": "Tài khoản của bạn đã bị tắt quyền chạy mã.",
        "sandbox.no_backend": "không có môi trường sandbox nào khả dụng",
    },
    "tr": {
        "api.content_required": "content alanı zorunludur",
        "api.invalid_channels_config": "Geçersiz kanal yapılandırması",
        "api.partner_already_exists": "'{name}' adlı partner zaten var",
        "api.partner_not_found": "Partner bulunamadı",
        "api.partner_not_found_or_not_running": "Partner bulunamadı veya çalışmıyor",
        "api.partner_not_running": "Partner çalışmıyor",
        "api.partner_stopped_start_required": "Partner durdurulmuş. Sohbete başlamadan önce başlatın.",
        "api.persona_already_exists": "Kişilik zaten var: {name}",
        "api.persona_name_required": "Kişilik adı gereklidir",
        "api.persona_not_found": "Kişilik bulunamadı: {name}",
        "api.soul_already_exists": "'{name}' adlı Soul zaten var",
        "api.soul_content_empty": "Özel Soul içeriği boş",
        "api.soul_library_not_found": "'{name}' adlı Soul kitaplıkta bulunamadı",
        "api.soul_not_found": "Soul bulunamadı",
        "api.tool_not_found": "'{name}' aracı bulunamadı",
        "cli_apps.abi_mismatch": (
            "{app!r} CLI uygulaması {installed} için kurulmuş, ancak bu çalışma zamanı {current} "
            "kullanıyor. Bir yöneticinin uygulamayı yeniden kurması gerekiyor."
        ),
        "cli_apps.args_required": "{tool} bir 'args' dizisi gerektirir — her öğede bir komut satırı argümanı olmalıdır.",
        "cli_apps.entry_admin_only": (
            "CLI uygulamalarını bir yönetici kurar; bu uygulamayı eklemesini yöneticinizden "
            "isteyin."
        ),
        "cli_apps.install_in_progress": "Bu uygulama zaten kuruluyor.",
        "cli_apps.not_in_catalog": "Katalogda {id!r} adlı CLI uygulaması yok.",
        "cli_apps.not_installed": "{app!r} CLI uygulaması artık bu dağıtımda kurulu değil.",
        "cli_apps.still_running": "{app} hâlâ çalışıyor ({seconds} sn)",
        "mcp.configure_command_or_url": "{name!r} sunucusu: bir komut (stdio) veya URL yapılandırın.",
        "mcp.configure_before_testing": "Test etmeden önce bir komut (stdio) veya URL yapılandırın.",
        "mcp.server_error": "{name!r} sunucusu: {error}",
        "mcp.server_missing": "Listenizde {name!r} adlı sunucu yok.",
        "mcp.not_oauth": "Bu sunucu OAuth kullanmıyor; bunun yerine kimlik bilgisi girin.",
        "mcp.oauth_callback_incomplete": "Yetkilendirme yanıtı eksikti.",
        "mcp.oauth_callback_unknown": "Bu yetkilendirmenin süresi dolmuş veya zaten tamamlanmış. Yeniden başlatın.",
        "mcp.oauth_done": "Yetkilendirme tamamlandı. Bu sekmeyi kapatabilirsiniz.",
        "mcp.oauth_failed": "Yetkilendirme başarısız oldu.",
        "mcp.catalog_entry_missing": "Katalogda {id!r} adlı MCP hizmeti yok.",
        "mcp.entry_admin_only": (
            "Bu hizmet yerel bir komut olarak çalışır ve yalnızca bir yönetici tarafından "
            "eklenebilir."
        ),
        "mcp.tool_not_available": (
            "Bu araç bu sohbette kullanılamıyor. Yalnızca prompt içinde listelenen araçlar "
            "çağrılabilir."
        ),
        "sandbox.command_blocked": "Hata: komut güvenlik denetimi tarafından engellendi (tehlikeli kalıp).",
        "sandbox.disabled_for_account": "Hesabınız için kod çalıştırma devre dışı bırakılmış.",
        "sandbox.no_backend": "Kullanılabilir korumalı alan (sandbox) arka ucu yok",
    },
    "pt": {
        "api.content_required": "content é obrigatório",
        "api.invalid_channels_config": "Configuração de canais inválida",
        "api.partner_already_exists": "O parceiro '{name}' já existe",
        "api.partner_not_found": "Parceiro não encontrado",
        "api.partner_not_found_or_not_running": "Parceiro não encontrado ou não está em execução",
        "api.partner_not_running": "Parceiro não está em execução",
        "api.partner_stopped_start_required": "O parceiro está parado. Inicie-o antes de conversar.",
        "api.persona_already_exists": "A persona já existe: {name}",
        "api.persona_name_required": "O nome da persona é obrigatório",
        "api.persona_not_found": "Persona não encontrada: {name}",
        "api.soul_already_exists": "O Soul '{name}' já existe",
        "api.soul_content_empty": "O conteúdo do Soul personalizado está vazio",
        "api.soul_library_not_found": "O Soul '{name}' não foi encontrado na biblioteca",
        "api.soul_not_found": "Soul não encontrado",
        "api.tool_not_found": "Ferramenta '{name}' não encontrada",
        "cli_apps.abi_mismatch": (
            "O app de CLI {app!r} foi instalado para {installed}, mas este ambiente é {current}. Um "
            "administrador precisa reinstalá-lo."
        ),
        "cli_apps.args_required": "{tool} precisa de uma lista 'args' — um argumento de linha de comando por elemento.",
        "cli_apps.entry_admin_only": (
            "Os apps de CLI são instalados por um administrador; peça ao seu administrador para "
            "adicionar este."
        ),
        "cli_apps.install_in_progress": "Esse app já está sendo instalado.",
        "cli_apps.not_in_catalog": "Nenhum app de CLI chamado {id!r} no catálogo.",
        "cli_apps.not_installed": "O app de CLI {app!r} não está mais instalado nesta implantação.",
        "cli_apps.still_running": "{app} ainda está em execução ({seconds}s)",
        "mcp.configure_command_or_url": "Servidor {name!r}: configure um comando (stdio) ou uma URL.",
        "mcp.configure_before_testing": "Configure um comando (stdio) ou uma URL antes de testar.",
        "mcp.server_error": "Servidor {name!r}: {error}",
        "mcp.server_missing": "Nenhum servidor chamado {name!r} na sua lista.",
        "mcp.not_oauth": "Este servidor não usa OAuth; forneça uma credencial.",
        "mcp.oauth_callback_incomplete": "A resposta de autorização está incompleta.",
        "mcp.oauth_callback_unknown": "Essa autorização expirou ou já foi concluída. Inicie-a novamente.",
        "mcp.oauth_done": "Autorizado. Você pode fechar esta aba.",
        "mcp.oauth_failed": "Falha na autorização.",
        "mcp.catalog_entry_missing": "Nenhum serviço MCP chamado {id!r} no catálogo.",
        "mcp.entry_admin_only": (
            "Este serviço é executado como um comando local e só pode ser adicionado por um "
            "administrador."
        ),
        "mcp.tool_not_available": (
            "Esta ferramenta não está disponível nesta conversa. Apenas as ferramentas listadas no "
            "prompt podem ser chamadas."
        ),
        "sandbox.command_blocked": "Erro: comando bloqueado pela proteção de segurança (padrão perigoso).",
        "sandbox.disabled_for_account": "A execução de código está desativada para a sua conta.",
        "sandbox.no_backend": "nenhum backend de ambiente isolado disponível",
    },
    "it": {
        "api.content_required": "content è obbligatorio",
        "api.invalid_channels_config": "Configurazione dei canali non valida",
        "api.partner_already_exists": "Il partner '{name}' esiste già",
        "api.partner_not_found": "Partner non trovato",
        "api.partner_not_found_or_not_running": "Partner non trovato o non in esecuzione",
        "api.partner_not_running": "Il partner non è in esecuzione",
        "api.partner_stopped_start_required": "Il partner è arrestato. Avvialo prima di iniziare a chattare.",
        "api.persona_already_exists": "La persona esiste già: {name}",
        "api.persona_name_required": "Il nome della persona è obbligatorio",
        "api.persona_not_found": "Persona non trovata: {name}",
        "api.soul_already_exists": "La Soul '{name}' esiste già",
        "api.soul_content_empty": "Il contenuto della Soul personalizzata è vuoto",
        "api.soul_library_not_found": "Soul '{name}' non trovata nella libreria",
        "api.soul_not_found": "Soul non trovata",
        "api.tool_not_found": "Strumento '{name}' non trovato",
        "cli_apps.abi_mismatch": (
            "L’applicazione CLI {app!r} è stata installata per {installed}, ma questo runtime usa "
            "{current}. Un amministratore deve reinstallare l’applicazione."
        ),
        "cli_apps.args_required": "{tool} richiede un array 'args' — un argomento della riga di comando per elemento.",
        "cli_apps.entry_admin_only": (
            "Le applicazioni CLI vengono installate da un amministratore; chiedi al tuo "
            "amministratore di aggiungere questa applicazione."
        ),
        "cli_apps.install_in_progress": "Questa applicazione è già in fase di installazione.",
        "cli_apps.not_in_catalog": "Nessuna applicazione CLI denominata {id!r} nel catalogo.",
        "cli_apps.not_installed": "L’applicazione CLI {app!r} non è più installata in questa distribuzione.",
        "cli_apps.still_running": "{app} è ancora in esecuzione ({seconds} s)",
        "mcp.configure_command_or_url": "Server {name!r}: configura un comando (stdio) o un URL.",
        "mcp.configure_before_testing": "Configura un comando (stdio) o un URL prima di eseguire il test.",
        "mcp.server_error": "Server {name!r}: {error}",
        "mcp.server_missing": "Nessun server denominato {name!r} nel tuo elenco.",
        "mcp.not_oauth": "Questo server non utilizza OAuth; fornisci invece una credenziale.",
        "mcp.oauth_callback_incomplete": "La risposta di autorizzazione era incompleta.",
        "mcp.oauth_callback_unknown": "Questa autorizzazione è scaduta o è già stata completata. Riavvia la procedura.",
        "mcp.oauth_done": "Autorizzazione completata. Puoi chiudere questa scheda.",
        "mcp.oauth_failed": "Autorizzazione non riuscita.",
        "mcp.catalog_entry_missing": "Nessun servizio MCP denominato {id!r} nel catalogo.",
        "mcp.entry_admin_only": (
            "Questo servizio viene eseguito come comando locale e può essere aggiunto solo da un "
            "amministratore."
        ),
        "mcp.tool_not_available": (
            "Questo strumento non è disponibile in questa chat. Possono essere chiamati solo gli "
            "strumenti elencati nel prompt."
        ),
        "sandbox.command_blocked": "Errore: comando bloccato dalla protezione di sicurezza (pattern pericoloso).",
        "sandbox.disabled_for_account": "L’esecuzione del codice è disabilitata per il tuo account.",
        "sandbox.no_backend": "Nessun backend sandbox disponibile",
    },
    "hu": {
        "api.content_required": "a content mező kötelező",
        "api.invalid_channels_config": "Érvénytelen csatornabeállítás",
        "api.partner_already_exists": "'{name}' nevű partner már létezik",
        "api.partner_not_found": "A partner nem található",
        "api.partner_not_found_or_not_running": "A partner nem található vagy nem fut",
        "api.partner_not_running": "A partner nem fut",
        "api.partner_stopped_start_required": "A partner le van állítva. Beszélgetés előtt indítsa el.",
        "api.persona_already_exists": "A persona már létezik: {name}",
        "api.persona_name_required": "A persona neve kötelező",
        "api.persona_not_found": "A persona nem található: {name}",
        "api.soul_already_exists": "A(z) '{name}' Soul már létezik",
        "api.soul_content_empty": "Az egyéni Soul tartalma üres",
        "api.soul_library_not_found": "A(z) '{name}' Soul nem található a könyvtárban",
        "api.soul_not_found": "A Soul nem található",
        "api.tool_not_found": "A(z) '{name}' eszköz nem található",
        "cli_apps.abi_mismatch": (
            "A(z) {app!r} CLI-alkalmazás {installed} rendszerre települt, de ez a futtatókörnyezet "
            "{current}. Egy adminisztrátornak újra kell telepítenie."
        ),
        "cli_apps.args_required": "{tool} egy 'args' tömböt vár — elemenként egy parancssori argumentumot.",
        "cli_apps.entry_admin_only": (
            "A CLI-alkalmazásokat adminisztrátor telepíti; kérje meg az adminisztrátort, hogy adja "
            "hozzá ezt is."
        ),
        "cli_apps.install_in_progress": "Ez az alkalmazás már telepítés alatt áll.",
        "cli_apps.not_in_catalog": "Nincs {id!r} nevű CLI-alkalmazás a katalógusban.",
        "cli_apps.not_installed": "A(z) {app!r} CLI-alkalmazás már nincs telepítve ezen a példányon.",
        "cli_apps.still_running": "{app} még fut ({seconds} s)",
        "mcp.configure_command_or_url": "{name!r} kiszolgáló: állítson be egy parancsot (stdio) vagy egy URL-t.",
        "mcp.configure_before_testing": "Tesztelés előtt állítson be egy parancsot (stdio) vagy egy URL-t.",
        "mcp.server_error": "{name!r} kiszolgáló: {error}",
        "mcp.server_missing": "Nincs {name!r} nevű kiszolgáló a listáján.",
        "mcp.not_oauth": "Ez a kiszolgáló nem használ OAuth-ot; adjon meg inkább egy hitelesítő adatot.",
        "mcp.oauth_callback_incomplete": "Az engedélyezési válasz hiányos volt.",
        "mcp.oauth_callback_unknown": "Ez az engedélyezés lejárt vagy már befejeződött. Indítsa újra.",
        "mcp.oauth_done": "Engedélyezve. Bezárhatja ezt a lapot.",
        "mcp.oauth_failed": "Az engedélyezés nem sikerült.",
        "mcp.catalog_entry_missing": "Nincs {id!r} nevű MCP-szolgáltatás a katalógusban.",
        "mcp.entry_admin_only": "Ez a szolgáltatás helyi parancsként fut, ezért csak adminisztrátor adhatja hozzá.",
        "mcp.tool_not_available": (
            "Ez az eszköz ebben a beszélgetésben nem érhető el. Csak a promptban felsorolt eszközök "
            "hívhatók."
        ),
        "sandbox.command_blocked": "Hiba: a biztonsági védelem letiltotta a parancsot (veszélyes minta).",
        "sandbox.disabled_for_account": "A kódvégrehajtás ki van kapcsolva az Ön fiókjában.",
        "sandbox.no_backend": "nincs elérhető sandbox-háttér",
    },
}


def current_language(default: str = "en") -> str:
    try:
        from deeptutor.services.settings.interface_settings import get_ui_language

        return _parse_language(get_ui_language(default=default))
    except Exception:
        return _parse_language(default)


def t(key: str, default: str = "", *, language: str | None = None, **kwargs: Any) -> str:
    lang = _parse_language(language) if language else current_language()
    text = _MESSAGES.get(lang, {}).get(key) or _MESSAGES["en"].get(key) or default
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


__all__ = ["current_language", "t"]
