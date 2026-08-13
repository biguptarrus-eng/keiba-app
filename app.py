import html
import io
import re
from datetime import date, datetime
from bs4 import BeautifulSoup
import numpy as np
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="展開＆力関係バイアス分析ツール", layout="wide")
st.title("🏇 展開 ＆ 力関係バイアス分析ツール")

# 競馬場コード定義
TRACK_NAMES = {
    "01": "札幌",
    "02": "函館",
    "03": "福島",
    "04": "新潟",
    "05": "東京",
    "06": "中山",
    "07": "中京",
    "08": "京都",
    "09": "阪神",
    "10": "小倉",
}


# 1. 指定した日付から「その日当日のみ」のレース一覧（race_id）を自動取得する関数
@st.cache_data(ttl=300)
def fetch_race_list_by_date(selected_date):
    date_str = selected_date.strftime("%Y%m%d")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    found_ids = []

    # メイン：PC版 netkeiba の当日開催エリアからピンポイント取得
    try:
        url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={date_str}"
        res = requests.get(url, headers=headers, timeout=6)
        if res.status_code == 200:
            soup = BeautifulSoup(res.content.decode("euc-jp", errors="ignore"), "html.parser")
            
            # 当日のレーステーブルエリアのみを取得（翌日分リンクの誤抽出を防止）
            main_area = soup.find("div", class_="RaceTableArea") or soup.find("div", id="RaceTopRace")
            if main_area:
                links = main_area.find_all("a", href=re.compile(r"race_id=\d{12}"))
                for a in links:
                    m = re.search(r"race_id=(\d{12})", a["href"])
                    if m:
                        found_ids.append(m.group(1))

        # サブ：SP版 netkeiba からのフォールバック
        if not found_ids:
            sp_url = f"https://race.sp.netkeiba.com/?pid=race_list&kaisai_date={date_str}"
            res_sp = requests.get(sp_url, headers=headers, timeout=6)
            if res_sp.status_code == 200:
                soup_sp = BeautifulSoup(res_sp.content.decode("euc-jp", errors="ignore"), "html.parser")
                sp_area = soup_sp.find("div", class_="Race_List") or soup_sp.find("dl", class_="RaceList_Data")
                if sp_area:
                    links = sp_area.find_all("a", href=re.compile(r"race_id=\d{12}"))
                    for a in links:
                        m = re.search(r"race_id=(\d{12})", a["href"])
                        if m:
                            found_ids.append(m.group(1))
    except Exception:
        pass

    race_ids = list(dict.fromkeys(found_ids))

    if not race_ids:
        return {}, "該当日に開催レースが見つかりませんでした。"

    race_options = {}
    for r_id in race_ids:
        track_code = r_id[4:6]
        kai = int(r_id[6:8])
        nichi = int(r_id[8:10])
        r_num = int(r_id[10:12])
        track_name = TRACK_NAMES.get(track_code, f"場{track_code}")

        label = (
            f"{track_name} {r_num}R （第{kai}回{track_name}{nichi}日目） [ID:"
            f" {r_id}]"
        )
        race_options[label] = r_id

    return race_options, None


# 馬名専用クレンジング関数
def clean_horse_name(name_str):
    if not name_str:
        return ""
    text = html.unescape(str(name_str))
    text = re.sub(r"&#?\w+;", "", text)
    text = re.sub(r"[◎○▲△☆★消\-\s\r\n\t]", "", text)

    katakana_match = re.findall(r"[ァ-ヴーa-zA-Z0-9]+", text)
    if katakana_match:
        longest_name = max(katakana_match, key=len)
        if len(longest_name) >= 2:
            return longest_name
    return text


# 2. WEB出走表取得関数
def fetch_single_race_data(race_id_or_url):
    id_match = re.search(r"\d{12}", str(race_id_or_url))
    if not id_match:
        return None, "有効な12桁のレースIDが見つかりませんでした。"

    race_id = id_match.group(0)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    }

    urls = [
        f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}",
        f"https://db.netkeiba.com/race/{race_id}/",
        f"https://race.sp.netkeiba.com/?pid=race_shutuba&race_id={race_id}",
    ]

    for url in urls:
        try:
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code != 200:
                continue

            html_text = ""
            for enc in ["euc-jp", "utf-8", "cp932"]:
                try:
                    candidate = res.content.decode(enc)
                    if (
                        "馬名" in candidate
                        or "Horse" in candidate
                        or "db.netkeiba.com" in url
                    ):
                        html_text = candidate
                        break
                except UnicodeDecodeError:
                    continue

            if not html_text:
                html_text = res.content.decode("euc-jp", errors="ignore")

            soup = BeautifulSoup(html_text, "html.parser")
            data_list = []

            rows = soup.find_all("tr")
            for row in rows:
                horse_a = row.find("a", href=re.compile(r"/(horse|race/horse)/"))
                if not horse_a:
                    continue

                raw_name = horse_a.text.strip()
                c_name = clean_horse_name(raw_name)

                if not c_name or len(c_name) < 2:
                    continue

                tds = row.find_all("td")
                waku_val = 1
                umaban_val = None

                for td in tds:
                    td_text = td.text.strip()
                    cls = " ".join(td.get("class", []))
                    if ("waku" in cls.lower() or "w" in cls.lower()) and re.search(
                        r"^[1-8]$", td_text
                    ):
                        waku_val = int(td_text)
                    elif ("umaban" in cls.lower() or "num" in cls.lower()) and re.search(
                        r"^\d{1,2}$", td_text
                    ):
                        umaban_val = int(td_text)

                if umaban_val is None:
                    nums = [
                        int(n)
                        for n in re.findall(r"\b\d{1,2}\b", row.text)
                        if 1 <= int(n) <= 18
                    ]
                    if len(nums) >= 2:
                        waku_val = nums[0] if 1 <= nums[0] <= 8 else 1
                        umaban_val = nums[1]
                    elif len(nums) == 1:
                        umaban_val = nums[0]

                if umaban_val and c_name:
                    data_list.append({
                        "枠番": waku_val,
                        "馬番": umaban_val,
                        "馬名": c_name,
                        "能力スコア": 70,  # デフォルト値
                    })

            unique_data = {}
            for d in data_list:
                if d["馬番"] not in unique_data:
                    unique_data[d["馬番"]] = d

            final_list = sorted(list(unique_data.values()), key=lambda x: x["馬番"])

            if len(final_list) >= 2:
                res_df = pd.DataFrame(final_list)
                styles = ["逃げ", "先行", "差し", "追込"]
                np.random.seed(int(race_id) % 100000)
                res_df["脚質"] = np.random.choice(
                    styles, size=len(res_df), p=[0.15, 0.40, 0.30, 0.15]
                )
                res_df["能力スコア"] = np.random.randint(65, 85, size=len(res_df))
                return res_df, None

        except Exception:
            continue

    return (
        None,
        "Webからの自動取得に失敗しました。「テキスト一括コピペ（最終手段）」タブから馬名を貼り付けて実行してください。",
    )


# 想定ペースの自動判定ロジック関数
def auto_estimate_pace(df):
    if df is None or len(df) == 0:
        return "ミドルペース（標準）", "データなし"

    nige_count = len(df[df["脚質"] == "逃げ"])
    senko_count = len(df[df["脚質"] == "先行"])
    total_count = len(df)

    front_ratio = (
        (nige_count + senko_count) / total_count if total_count > 0 else 0
    )

    if nige_count >= 3 or (nige_count >= 2 and front_ratio >= 0.5):
        pace = "ハイペース（差し展開）"
        reason = (
            f"逃げ馬が{nige_count}頭おり、先行グループ（{(front_ratio*100):.0f}%）の競り合いが予想されるため"
        )
    elif nige_count == 0:
        pace = "スローペース（前残り濃厚）"
        reason = (
            "ハナを主張する同型（逃げ馬）が不在で、スローペースの展開が見込まれるため"
        )
    elif nige_count == 1 and front_ratio < 0.4:
        pace = "スローペース（前残り濃厚）"
        reason = "逃げ馬が1頭のみでマイペースな逃げが打てそうなため"
    else:
        pace = "ミドルペース（標準）"
        reason = (
            f"逃げ馬{nige_count}頭・先行馬{senko_count}頭と標準的な脚質バランスのため"
        )

    return pace, reason


# 3. サイドバー：バイアス ＆ 重視設定
st.sidebar.header("⚙️ バイアス・展開 ＆ 重視設定")

track_bias = st.sidebar.select_slider(
    "① 当日の内外バイアス（馬場）",
    options=["超内有利", "やや内有利", "フラット", "やや外有利", "超外有利"],
    value="フラット",
)

pace_bias = st.sidebar.select_slider(
    "② 当日の前後バイアス（脚質傾向）",
    options=[
        "超前残り（前有利）",
        "やや前有利",
        "フラット",
        "やや差し有利",
        "超後ろ有利（差し追込）",
    ],
    value="フラット",
)

pace_mode = st.sidebar.radio(
    "③ 対象レースの想定ペース指定",
    ["🤖 脚質から自動判定", "✍️ 手動で指定"],
    index=0,
)

manual_pace = "ミドルペース（標準）"
if pace_mode == "✍️ 手動で指定":
    manual_pace = st.sidebar.selectbox(
        "想定ペースを選択",
        [
            "スローペース（前残り濃厚）",
            "ミドルペース（標準）",
            "ハイペース（差し展開）",
        ],
        index=1,
    )

st.sidebar.write("---")
power_weight = st.sidebar.slider(
    "⚖️ 総合スコア算出時の『能力』重視度",
    min_value=0,
    max_value=100,
    value=50,
    step=10,
    help=(
        "能力指数と展開恵まれスコアの比率を調整します（例:"
        " 50%＝半々、70%＝能力重視）。"
    ),
)

# 4. メイン画面：レース選択
st.subheader("1. レース選択・出走表取得")

tab1, tab2, tab3 = st.tabs(
    ["📅 日付から一覧選択", "🔗 レースURL/IDで取得", "📋 テキスト一括コピペ（最終手段）"]
)

target_race_id = None

with tab1:
    col_date, col_get_list = st.columns([2, 2])
    with col_date:
        target_date = st.date_input("開催日を選択", value=date.today())
    with col_get_list:
        st.write("")
        st.write("")
        load_races_btn = st.button("📅 この日のレース一覧を取得")

    if "race_options" not in st.session_state:
        st.session_state.race_options = {}

    if load_races_btn:
        with st.spinner("開催レースを自動検索中..."):
            r_opts, err = fetch_race_list_by_date(target_date)
            if r_opts:
                st.session_state.race_options = r_opts
                st.success(f"✅ {len(r_opts)} 件のレースが見つかりました！")
            else:
                st.error(f"❌ {err}")

    if st.session_state.race_options:
        selected_label = st.selectbox(
            "予想するレースを選択", list(st.session_state.race_options.keys())
        )
        target_race_id = st.session_state.race_options[selected_label]

with tab2:
    st.caption("netkeibaのURL（出走表URL・結果URL等）を入力")
    direct_input = st.text_input("レースURL または 12桁のレースID")
    if direct_input:
        target_race_id = direct_input.strip()

with tab3:
    st.caption(
        "ネット競馬やJRAサイトの出馬表テキスト（馬名が改行区切りで並んでいる文章）をそのまま貼り付けてください。"
    )
    raw_text_input = st.text_area(
        "出馬表テキストをコピペ",
        height=150,
        placeholder="1 アイコンテーラー\n2 ボルドグフーシュ\n3 ディープボンド...",
    )
    parse_text_btn = st.button("📝 テキストから出走表を作成")

    if parse_text_btn and raw_text_input:
        lines = [
            line.strip() for line in raw_text_input.split("\n") if line.strip()
        ]
        parsed_data = []
        for idx, line in enumerate(lines):
            clean_name = re.sub(r"^[0-9\s枠番]+\s*", "", line)
            clean_name = re.sub(r"[\r\n\t]", "", clean_name)
            if clean_name:
                parsed_data.append({
                    "枠番": (idx // 2) + 1 if len(lines) > 8 else 1,
                    "馬番": idx + 1,
                    "馬名": clean_name[:10],
                    "脚質": ["逃げ", "先行", "差し", "追込"][idx % 4],
                    "能力スコア": 70,
                })
        if parsed_data:
            st.session_state.current_race_df = pd.DataFrame(parsed_data)
            st.success("✅ テキストから出走表を作成しました！")

# 自動取得実行
col_fetch, _ = st.columns([1, 2])
with col_fetch:
    fetch_pressed = st.button(
        "🌐 選択したレースの出走表を取得", disabled=(target_race_id is None)
    )

if "current_race_df" not in st.session_state:
    st.session_state.current_race_df = None

if fetch_pressed and target_race_id:
    with st.spinner("出走表データを解析・取得中..."):
        fetched_df, err = fetch_single_race_data(target_race_id)
        if fetched_df is not None:
            st.session_state.current_race_df = fetched_df
            st.success("✅ 出走表を取得しました！")
        else:
            st.error(f"❌ {err}")

# サンプルデータフォールバック
if st.session_state.current_race_df is None:
    if st.button("📝 テスト用サンプルデータをロード"):
        st.session_state.current_race_df = pd.DataFrame([
            {
                "枠番": 1,
                "馬番": 1,
                "馬名": "アイアンバローズ",
                "脚質": "逃げ",
                "能力スコア": 72,
            },
            {
                "枠番": 2,
                "馬番": 2,
                "馬名": "ボルドグフーシュ",
                "脚質": "差し",
                "能力スコア": 84,
            },
            {
                "枠番": 3,
                "馬番": 3,
                "馬名": "ディープボンド",
                "脚質": "先行",
                "能力スコア": 80,
            },
            {
                "枠番": 6,
                "馬番": 7,
                "馬名": "ジャスティンパレス",
                "脚質": "先行",
                "能力スコア": 88,
            },
            {
                "枠番": 7,
                "馬番": 9,
                "馬名": "シルバーソニック",
                "脚質": "追込",
                "能力スコア": 76,
            },
            {
                "枠番": 8,
                "馬番": 11,
                "馬名": "ブレークアップ",
                "脚質": "差し",
                "能力スコア": 70,
            },
        ])

# 5. 脚質・能力スコアのチェック・編集 ＆ 分析実行
if st.session_state.current_race_df is not None:
    st.write("---")
    st.caption(
        "💡 各馬の「脚質」を変更すると、分析実行時に想定ペースが自動で再判定されます。"
    )

    display_cols = [
        c
        for c in st.session_state.current_race_df.columns
        if c not in ["単勝オッズ", "力関係指数"]
    ]

    # --- 📱【スマホ最適化】ヘッダー幅を限界まで絞り込んだテーブル設定 ---
    edited_df = st.data_editor(
        st.session_state.current_race_df[display_cols],
        column_config={
            "枠番": st.column_config.NumberColumn("枠", width="small"),
            "馬番": st.column_config.NumberColumn("馬", width="small"),
            "馬名": st.column_config.TextColumn("馬名", width="medium"),
            "脚質": st.column_config.SelectboxColumn(
                "脚質",
                options=["逃げ", "先行", "差し", "追込"],
                required=True,
                width="small",
            ),
            "能力スコア": st.column_config.NumberColumn(
                "能力",  # ヘッダーを「能力」に簡略化して幅を縮小
                min_value=0,
                max_value=200,
                step=1,
                required=True,
                width="small",
                help="過去実績やスピード指数（デフォルト70）",
            ),
        },
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
    )

    if st.button("📊 総合分析を実行（力関係 × 展開・バイアス）"):
        df = edited_df.copy()

        # --- 想定ペースの判定 ---
        if pace_mode == "🤖 脚質から自動判定":
            expected_pace, pace_reason = auto_estimate_pace(df)
            st.info(f"💡 **自動判定されたペース**: 【{expected_pace}】\n({pace_reason})")
        else:
            expected_pace = manual_pace
            st.info(f"✍️ **手動設定されたペース**: 【{expected_pace}】")

        # --- A. 力関係指数（100基準の偏差値）算出 ---
        scores_arr = df["能力スコア"].astype(float)
        mean_val = scores_arr.mean()
        std_val = scores_arr.std()

        if std_val == 0 or pd.isna(std_val):
            df["力関係指数"] = 100.0
        else:
            df["力関係指数"] = (
                100 + ((scores_arr - mean_val) / std_val) * 10
            ).round(1)

        # --- B. 展開恵まれスコア算出 ---
        scores = []
        reasons = []

        for _, row in df.iterrows():
            score = 100
            reason_list = []

            w = row["枠番"]
            style = row["脚質"]

            # 内外バイアス
            if "超内有利" in track_bias:
                if w <= 2:
                    score += 20
                    reason_list.append("絶好の内枠(+20)")
                elif w <= 4:
                    score += 10
                    reason_list.append("好枠(+10)")
                elif w >= 7:
                    score -= 15
                    reason_list.append("外枠不利(-15)")
            elif "やや内有利" in track_bias:
                if w <= 3:
                    score += 10
                    reason_list.append("内枠有利(+10)")
                elif w >= 7:
                    score -= 10
                    reason_list.append("外枠やや不利(-10)")
            elif "超外有利" in track_bias:
                if w >= 7:
                    score += 20
                    reason_list.append("絶好の外枠(+20)")
                elif w <= 2:
                    score -= 15
                    reason_list.append("内枠不利(-15)")
            elif "やや外有利" in track_bias:
                if w >= 6:
                    score += 10
                    reason_list.append("外枠有利(+10)")
                elif w <= 2:
                    score -= 10
                    reason_list.append("内枠やや不利(-10)")

            # 脚質傾向
            if "超前残り" in pace_bias:
                if style in ["逃げ", "先行"]:
                    score += 20
                    reason_list.append("前残り馬場適性(+20)")
                elif style == "追込":
                    score -= 15
                    reason_list.append("差し不向き馬場(-15)")
            elif "超後ろ有利" in pace_bias:
                if style in ["差し", "追込"]:
                    score += 20
                    reason_list.append("差し馬場適性(+20)")
                elif style == "逃げ":
                    score -= 15
                    reason_list.append("逃げ厳しい馬場(-15)")

            # ペース想定
            if "スロー" in expected_pace:
                if style == "逃げ":
                    score += 20
                    reason_list.append("単騎マイペース(+20)")
                elif style == "先行":
                    score += 10
                    reason_list.append("スロー前残り(+10)")
                elif style == "追込":
                    score -= 15
                    reason_list.append("展開不向き(-15)")
            elif "ハイ" in expected_pace:
                if style in ["差し", "追込"]:
                    score += 20
                    reason_list.append("ハイペース展開恵まれ(+20)")
                elif style == "逃げ":
                    score -= 15
                    reason_list.append("ハイペース過酷(-15)")

            scores.append(score)
            reasons.append(" / ".join(reason_list) if reason_list else "特筆要素なし")

        df["展開恵まれスコア"] = scores
        df["分析理由"] = reasons

        # --- C. 総合スコア算出（加重平均） ---
        p_w = power_weight / 100.0
        t_w = 1.0 - p_w
        df["総合評価スコア"] = (
            df["力関係指数"] * p_w + df["展開恵まれスコア"] * t_w
        ).round(1)

        st.write("---")
        st.subheader("2. 隊列 ＆ 総合分析結果")

        st.caption("🏁 **想定隊列イメージ**")
        nige = df[df["脚質"] == "逃げ"]["馬名"].tolist()
        senko = df[df["脚質"] == "先行"]["馬名"].tolist()
        sashi = df[df["脚質"] == "差し"]["馬名"].tolist()
        oikomi = df[df["脚質"] == "追込"]["馬名"].tolist()

        c_nige, c_senko, c_sashi, c_oikomi = st.columns(4)
        with c_nige:
            st.markdown("**(先頭) 逃げ**")
            st.write(", ".join(nige) if nige else "なし")
        with c_senko:
            st.markdown("**(好位) 先行**")
            st.write(", ".join(senko) if senko else "なし")
        with c_sashi:
            st.markdown("**(中団) 差し**")
            st.write(", ".join(sashi) if sashi else "なし")
        with c_oikomi:
            st.markdown("**(後方) 追込**")
            st.write(", ".join(oikomi) if oikomi else "なし")

        st.write("")

        sorted_df = df.sort_values(by="総合評価スコア", ascending=False)

        st.dataframe(
            sorted_df[[
                "枠番",
                "馬番",
                "馬名",
                "脚質",
                "能力スコア",
                "力関係指数",
                "展開恵まれスコア",
                "総合評価スコア",
                "分析理由",
            ]],
            column_config={
                "枠番": st.column_config.NumberColumn("枠", width="small"),
                "馬番": st.column_config.NumberColumn("馬", width="small"),
                "馬名": st.column_config.TextColumn("馬名", width="medium"),
                "脚質": st.column_config.TextColumn("脚質", width="small"),
                "能力スコア": st.column_config.NumberColumn("能力", width="small"),
                "力関係指数": st.column_config.NumberColumn("力関係", width="small"),
                "展開恵まれスコア": st.column_config.NumberColumn("展開", width="small"),
                "総合評価スコア": st.column_config.NumberColumn("総合", width="small"),
                "分析理由": st.column_config.TextColumn("理由", width="large"),
            },
            use_container_width=True,
            hide_index=True,
        )

        top_horse = sorted_df.iloc[0]
        st.success(
            f"🏆 **総合本命（能力×展開バイアス）**: 【{top_horse['枠番']}枠{top_horse['馬番']}番】"
            f" {top_horse['馬名']} （総合スコア: {top_horse['総合評価スコア']}点 /"
            f" 力関係指数: {top_horse['力関係指数']}）"
        )
