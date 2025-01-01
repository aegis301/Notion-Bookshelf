# %% [markdown]
# ## Setup

# %%
import pandas as pd
import requests
import json
import os
import datetime

from dotenv import load_dotenv


load_dotenv()

NOTION_SECRET = os.getenv("NOTION_SECRET")
DATABASE_ID = os.getenv("DATABASE_ID")
ONLY_NEW = False

# %% [markdown]
# ## Get data from google

# %%


def google_book_search(title, author, publisher):
    search_terms = " ".join(filter(None, [title, author, publisher]))
    url = 'https://www.googleapis.com/books/v1/volumes?q='
    response = requests.get(url + search_terms)
    data = response.json()
    # Normalizing data
    df = pd.json_normalize(data, record_path=['items'])
    return df


def query_databases(secret_key, database_id):
    url = "https://api.notion.com/v1/databases/" + database_id + '/query'

    payload = {'id': database_id}
    headers = {
        'Notion-Version': '2021-05-13',
        'Authorization': 'Bearer ' + secret_key
    }

    response = requests.request(
        "POST", url, headers=headers, data=payload)
    print(f"The response code is {response.status_code}")
    if response.status_code != 200:
        raise Exception(response.status_code, response.text)
    else:
        return response.json()


# %%
res = query_databases(NOTION_SECRET, DATABASE_ID)

# %%
notion_columns = ['Category', 'Publisher', 'Summary', 'Link',
                  'Total pages', 'Date started', 'Author', 'Title', 'url', 'page_id']
notion = pd.DataFrame(columns=notion_columns)
print(notion.columns)
print(notion.head())
for page in res.get('results'):
    properties = page.get('properties')
    try:
        author = properties.get('Author').get('rich_text')[0].get('plain_text')
    except IndexError:
        author = None
    try:
        title = properties.get('Title').get('title')[0].get('plain_text')
    except IndexError:
        title = None
    try:
        publisher = properties['Publisher']['select']['name']
    except KeyError:
        publisher = None
    try:
        category = properties['Category']['select']['name']
    except KeyError:
        category = None
    try:
        summary = properties['Summary']['rich_text'][0]['plain_text']
    except IndexError:
        summary = None
    try:
        link = properties['Link']['url']
    except KeyError:
        link = None
    try:
        total_pages = properties['Total pages']['number']
    except KeyError:
        total_pages = None
    try:
        date_started = properties['Date started']['date']['start']
    except KeyError:
        date_started = None

    url = page.get('url')
    page_id = url[-32:]
    # concat the data
    notion = pd.concat([notion, pd.DataFrame([[category, publisher, summary, link, total_pages,
                       date_started, author, title, url, page_id]], columns=notion_columns)], ignore_index=True)
# drop rows without title
notion = notion.dropna(subset=['Title'])

# %%
# move any old notion exports into a exports folder
if not os.path.exists('exports'):
    os.makedirs('exports')
    print("Created exports folder")

for file in os.listdir():
    if file.startswith('notion-') and file.endswith('.csv'):
        os.rename(file, f'exports/{file}')
# write to csv
notion.to_csv(
    f'notion-{datetime.datetime.now().strftime("%Y-%m-%d")}.csv', index=False)

# %%


def get_new_books(notion_df):
    # compare the current notion state with the last export to see if there are any new books

    # get the latest export sorted by date
    exports = os.listdir('exports')
    # extract dates
    dates = [export.split('-', maxsplit=1)[1].split('.')[0]
             for export in exports]
    # convert to datetime objects
    dates = [datetime.datetime.strptime(date, '%Y-%m-%d') for date in dates]
    # get the latest date
    try:
        latest = max(dates)
    except ValueError:
        print("No previous exports found")
        return notion_df
    # get the latest export
    latest_export = pd.read_csv(
        f'exports/notion-{latest.strftime("%Y-%m-%d")}.csv')
    # compare current notion state with latest export
    # get the titles of the latest export
    latest_titles = latest_export['Title'].values
    # get the titles of the current notion state
    current_titles = notion_df['Title'].values
    # get the titles that are in the current notion state but not in the latest export
    new_titles = [
        title for title in current_titles if title not in latest_titles]
    # get the rows of the new titles
    new_books = notion_df[notion_df['Title'].isin(new_titles)]
    return new_books


if ONLY_NEW is True:
    notion = get_new_books(notion)

# %%
google_results = pd.DataFrame()

for book in notion.itertuples():

    google_results = pd.concat([google_results, google_book_search(
        book.Title, book.Author, book.Publisher)], ignore_index=True)

# %%

google_data = google_results[['selfLink', 'volumeInfo.title',
                              'volumeInfo.subtitle', 'volumeInfo.authors', 'volumeInfo.publisher',
                              'volumeInfo.publishedDate', 'volumeInfo.description', 'volumeInfo.pageCount', 'volumeInfo.categories',
                              'volumeInfo.imageLinks.smallThumbnail', 'volumeInfo.imageLinks.thumbnail', 'saleInfo.country', 'saleInfo.retailPrice.amount',
                              'saleInfo.retailPrice.currencyCode'
                              ]]

# %%


def clean_google_data(google_df, notion_df):
    filtered_results = []

    for _, notion_row in notion_df.iterrows():
        try:
            # Convert Notion title to lowercase and split into words
            notion_title_words = notion_row['Title'].lower().split()
            # Create a regex pattern to match all words
            pattern = '.*'.join(notion_title_words)
            # Filter google_df by title using regex
            matches = google_df[google_df['volumeInfo.title'].str.lower(
            ).str.contains(pattern, regex=True, na=False)]
            # add page_id to matches as column
            matches['page_id'] = notion_row['page_id']

        except TypeError as e:
            print(f"TypeError encountered while filtering by title: {e}")
            continue

        try:
            # Further filter by author if available
            if pd.notna(notion_row.get('Author')):
                tmp_df = matches[matches['volumeInfo.authors'].apply(
                    lambda authors: notion_row['Author'] in authors if isinstance(authors, list) else False)]
                if not tmp_df.empty:  # If there are matches, keep them
                    matches = tmp_df
        except TypeError as e:
            print(f"TypeError encountered while filtering by author: {e}")
            continue

        try:
            # Further filter by publisher if available
            if pd.notna(notion_row.get('Publisher')):
                tmp_df = matches[matches['volumeInfo.publisher'].apply(
                    lambda publisher: notion_row['Publisher'] == publisher if isinstance(publisher, str) else False)]
                if not tmp_df.empty:  # If there are matches, keep them
                    matches = tmp_df

        except TypeError as e:
            print(f"TypeError encountered while filtering by publisher: {e}")
            continue
        try:
            # If there are matches, keep the latest by published_date
            latest_match = matches.sort_values(
                by='volumeInfo.publishedDate', ascending=False).iloc[0]
            filtered_results.append(latest_match)
        except IndexError:
            # only single match, append
            if not matches.empty:
                filtered_results.append(matches)
            else:
                print(f"No match found for {notion_row['Title']}")
            continue
        except KeyError:
            if not matches.empty:
                filtered_results.append(matches)
            else:
                print(f"No match found for {notion_row['Title']}")
            continue

    # Convert the list of filtered results to a DataFrame
    filtered_df = pd.DataFrame(filtered_results)
    return filtered_df


# %%
clean_google_data = clean_google_data(google_data, notion)
clean_google_data

# %%
# merge clean google and notion data on page_id
complete = pd.merge(notion, clean_google_data, on='page_id', how='left')
complete

# %% [markdown]
# ## Update Notion with Google Data

# %%
# Update a property on a page based on property type


def update_page(row, property_name, property_type, data_column, verbose=False):
    url = f"https://api.notion.com/v1/pages/{row.page_id}"

    # erxtract the property value
    property_value = row[data_column]

    # Check the property type and create the payload
    if property_type == 'date':
        property_payload = {
            "start": property_value
        }
    elif property_type == 'url':
        property_payload = property_value
    elif property_type == 'number':
        property_payload = property_value
    elif property_type == 'rich_text':
        property_payload = [{
            "type": "text",
            "text": {
                "content": property_value
            }
        }]
    elif property_type == 'select':
        property_payload = {
            "name": property_value
        }

    payload = json.dumps({
        "properties": {
            property_name: {
                property_type: property_payload
            }
        }
    })

    headers = {
        'Content-Type': 'application/json',
        'Notion-Version': '2021-05-13',
        'Authorization': f'Bearer {NOTION_SECRET}'
    }

    response = requests.request(
        "PATCH", url, headers=headers, data=payload)
    if verbose:
        print(response.status_code)
    errors = []
    if response.status_code != 200:
        errors.append(response.text)
    return errors

# %% [markdown]
# ### update publishing dates


# %%
clean_google_data.apply(lambda row: update_page(
    row, "Published", "date", "volumeInfo.publishedDate"), axis=1)

# %%
clean_google_data.apply(lambda row: update_page(
    row, "Link", "url", "selfLink"), axis=1)

# %%
clean_google_data.apply(lambda row: update_page(
    row, "Publisher", "select", "volumeInfo.publisher"), axis=1)

# %%
clean_google_data.apply(lambda row: update_page(
    row, "Total pages", "number", "volumeInfo.pageCount"), axis=1)

# %%
summary_errors = clean_google_data.apply(lambda row: update_page(
    row, "Summary", "rich_text", "volumeInfo.description"), axis=1)

# %%


def update_page_icon(row, data_column, icon_or_cover):

    page_id = row['page_id']
    property_value = row[data_column]

    url = f"https://api.notion.com/v1/pages/{page_id}"

    payload = json.dumps({icon_or_cover: {
        "type": "external",
        "external": {
                "url": property_value
        }}})
    headers = {
        'Content-Type': 'application/json',
        'Notion-Version': '2021-05-13',
        'Authorization': f'Bearer {NOTION_SECRET}'
    }

    response = requests.request(
        "PATCH", url, headers=headers, data=payload)
    errors = []
    if response.status_code != 200:
        print(payload)
        errors.append(response.text)
    return errors


# %%
icon_errors = clean_google_data.apply(lambda row: update_page_icon(
    row, 'volumeInfo.imageLinks.smallThumbnail', 'icon'), axis=1)

# %%
cover_errors = clean_google_data.apply(lambda row: update_page_icon(
    row, 'volumeInfo.imageLinks.smallThumbnail', 'cover'), axis=1)
