import json
import datetime
import os
import sys
import re
import urllib.parse
import urllib.request

try:
    import requests
except ImportError:
    print("[오류] pip install requests 명령어로 라이브러리를 설치해주세요.")
    sys.exit(1)

# ==============================================================================
# [필수 설정] 발급받은 공공데이터포털(data.go.kr) OpenAPI 일반 인증키(Encoding)
API_KEY = "2028aec1a978dcf1c46d62dfb9923edf87fe6ee5e101f63990319220c4504928"
# ==============================================================================

def fetch_g2b_api(keyword, target_clients=None):
    """
    공공데이터포털 조달청 입찰공고정보 OpenAPI를 활용하여 데이터를 수집합니다.
    """
    client_info = f" (공기업 우대: {', '.join(target_clients)})" if target_clients else ""
    print(f"[*] OpenAPI 데이터 수집 중... 키워드: {keyword}{client_info}")
    
    results = []
    
    # 최근 6개월 데이터 조회 기간 설정 (YYYYMMDDHHMM)
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    end_dt = now.strftime('%Y%m%d2359')
    start_dt = (now - datetime.timedelta(days=180)).strftime('%Y%m%d0000')

    # OpenAPI 엔드포인트 URL
    url = "http://apis.data.go.kr/1230000/BidPublicInfoService04/getBidPblancListInfoServc01"
    
    encoded_keyword = urllib.parse.quote(keyword.encode('utf-8'))
    query_params = (
        f"?ServiceKey={API_KEY}"
        f"&pageNo=1&numOfRows=50"
        f"&inqryDiv=1"
        f"&inqryBgnDt={start_dt}"
        f"&inqryEndDt={end_dt}"
        f"&bidNtceNm={encoded_keyword}"
        f"&type=json"
    )
    
    full_url = url + query_params

    try:
        response = requests.get(full_url, timeout=15)
        # API 인증키 오류나 동기화 전일 경우 500에러 등은 무시하고 Fallback으로 넘김
        if response.status_code != 200 or 'Unexpected errors' in response.text:
            print(f"[!] API 서버 오류(500) 또는 인증키 미동기화 상태입니다. 우회 스크래핑(Fallback)을 시도합니다.")
            return fetch_g2b_fallback(keyword, target_clients)
            
        response.raise_for_status()
        
        try:
            data = response.json()
            items = data.get('response', {}).get('body', {}).get('items', [])
        except json.JSONDecodeError:
            print(f"[-] 데이터 형식을 읽을 수 없습니다. (키워드: {keyword})")
            return []
            
        if not items:
            return []
            
        # JSON 결과 배열이 하나일 경우 dict로 넘길 수 있어서 리스트로 통일
        if isinstance(items, dict) and 'item' in items:
             # 데이터포털 xml->json 변환 특성 대응
             item_list = items['item']
             if isinstance(item_list, dict):
                 items = [item_list]
             else:
                 items = item_list
        else:
             items = items if isinstance(items, list) else []
             
        for item in items:
            # 주요 필드 매핑
            title = item.get('bidNtceNm', '')
            client = item.get('dminstNm', '')      # 수요기관명
            ntce_no = item.get('bidNtceNo', '')     # 공고번호
            reg_dt = item.get('bidNtceDt', '')      # 공고일시 (YYYY-MM-DD HH:MM:SS)
            deadline_str = item.get('bidClseDt', '') # 입찰마감일시
            bgn_dt = item.get('bidBgnDt', '')       # 입찰시작일시
            url_link = item.get('bidNtceDtlUrl', '')# 공고상세 URL
            
            # 날짜 포맷 변경 (YYYY-MM-DD)
            date_str = reg_dt[:10] if len(reg_dt) >= 10 else reg_dt
            
            probability = "중간"
            tags = [keyword]
            
            if target_clients:
                is_main_client = any(tc in client for tc in target_clients)
                if is_main_client:
                    probability = "높음"
                    tags.append("주요 공기업 발주")

            results.append({
                "date": date_str,
                "deadline": deadline_str,
                "number": ntce_no,
                "status": "진행중",
                "status_class": "ing",
                "title": title,
                "source": "조달청 OpenAPI",
                "client": client,
                "location": "전국", # OpenAPI 기본값 (상세 지역 코드는 별도 파싱 필요)
                "scale": "명시되지 않음",
                "content": title,
                "link": url_link,
                "link_hint": "원문 링크 클릭 시 상세 내역 확인 가능",
                "tags": tags,
                "probability": probability
            })

    except Exception as e:
        print(f"[!] {keyword} 연동 과정 중 오류 발생: {e}")
        return fetch_g2b_fallback(keyword, target_clients)

    return results

def fetch_g2b_fallback(keyword, target_clients):
    """
    OpenAPI가 500 상태이거나 오류가 있을 경우, 기존 일반 검색 통로(443 포트)로 우회 파싱합니다.
    """
    print(f"[*] (우회) 일반 G2B 웹 스크래핑 시도 중... 키워드: {keyword}")
    results = []
    
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    encoded_keyword = urllib.parse.quote(keyword.encode('euc-kr'))
    url = f"https://www.g2b.go.kr/ep/tbid/tbidList.do?searchType=1&bidNm={encoded_keyword}&searchDtType=1&recordCountPerPage=30"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }

    try:
        response = requests.get(url, headers=headers, timeout=15, verify=False)
        response.raise_for_status()
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, 'html.parser')
        
        tbody = soup.find('tbody')
        if not tbody:
            return results
            
        rows = tbody.find_all('tr')
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 5:
                continue
                
            try:
                date_str = cols[1].text.strip()
                title_col = cols[3].find('a')
                if not title_col:
                    continue
                title = title_col.text.strip()
                link = title_col['href'] if title_col.has_attr('href') else ""
                
                if link and not link.startswith("http"):
                    link = "https://www.g2b.go.kr" + link

                client = cols[5].text.strip()
                deadline_str = cols[7].text.strip()
                
                probability = "중간"
                tags = [keyword]
                
                if target_clients:
                    if any(tc in client for tc in target_clients):
                        probability = "높음"
                        tags.append("주요 공기업 발주")

                results.append({
                    "date": date_str, "deadline": deadline_str, "number": "웹수집건",
                    "status": "진행중", "status_class": "ing", "title": title,
                    "source": "나라장터 (G2B)", "client": client, "location": "링크참조",
                    "scale": "명시되지 않음", "content": title, "link": link,
                    "link_hint": "원문 링크 클릭", "tags": tags, "probability": probability
                })
            except Exception:
                pass
    except Exception as e:
        print(f"[-] 우회 스크래핑 실패: {e}")

    return results

def filter_active_data(data_list):
    """
    현재 시간 기준으로 마감일이 안 지난("진짜 진행중") 공고만 살려서 반환합니다.
    """
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    filtered_list = []
    
    for item in data_list:
        deadline_str = item.get("deadline", "")
        # OpenAPI 날짜형식: 2026-02-10 10:00:00
        if not deadline_str:
            filtered_list.append(item)
            continue
            
        match = re.search(r'(\d{4})[./-](\d{1,2})[./-](\d{1,2})\s*(\d{1,2})?:?(\d{1,2})?', str(deadline_str))
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            hour = int(match.group(4)) if match.group(4) else 23
            minute = int(match.group(5)) if match.group(5) else 59
            
            try:
                target_date = datetime.datetime(year, month, day, hour, minute)
                if target_date >= now:
                    filtered_list.append(item)
            except ValueError:
                filtered_list.append(item)
        else:
            filtered_list.append(item)
            
    return filtered_list

def main():
    now_kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    updated_time_str = now_kst.strftime("%Y년 %m월 %d일 %H:%M")
    
    print(f"업데이트 시작 시간: {updated_time_str}")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(base_dir, 'data.json')

    existing_data = {
        "last_updated": updated_time_str,
        "bids": [],
        "pre_specs": [],
        "plans": [],
        "top3": []
    }

    keywords = ["모듈러", "생활관", "기숙사", "병영생활관"]
    target_clients = ["SH", "서울주택도시공사", "IH", "인천도시공사", "LH", "한국토지주택공사", "GH", "경기주택도시공사"]
    
    print("------------------------------------------------------------")
    for keyword in keywords:
        new_g2b = fetch_g2b_api(keyword, target_clients)
        existing_data["bids"].extend(new_g2b)

    # 마감일 지난 공고 진짜로 빼버리기
    existing_data["bids"] = filter_active_data(existing_data.get("bids", []))
    
    # 중복 제거
    seen_titles = set()
    unique_bids = []
    for bid in existing_data["bids"]:
        if bid["title"] not in seen_titles:
            seen_titles.add(bid["title"])
            unique_bids.append(bid)
    existing_data["bids"] = unique_bids

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=2)

    print(f"------------------------------------------------------------")
    print(f"대시보드 정보 업데이트 완료! (최근 기준일: {updated_time_str})")
    print(f"총 {len(existing_data['bids'])}건의 유효한 공고가 수집되었습니다.")
    print(f"============================================================")

if __name__ == "__main__":
    main()
