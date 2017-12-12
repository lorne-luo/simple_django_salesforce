import requests
import magic
import json
import logging
import pytz
from datetime import datetime, timedelta
from django.utils import timezone
from django.conf import settings
from simple_salesforce import SalesforceResourceNotFound

log = logging.getLogger(__name__)
DEFAULT_API_VERSION = '38.0'
token_expiry = 110 * 60  # salesforce's default api token expiry is 2hr


class Chatter(object):
    def __init__(self):
        self.client_id = settings.CHATTER_OAUTH_CLIENT_ID
        self.client_secret = settings.CHATTER_OAUTH_CLIENT_SECRET
        self.username = settings.SALESFORCE_API_USER
        self.password = settings.SALESFORCE_API_PASSWORD
        self.api_token = settings.SALESFORCE_API_TOKEN
        self.access_token, self.instance_url, self.id_url, self.token_type,\
        self.issued_at, self.signature = self.login()

    def login(self):
        # https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/quickstart_connecting.htm
        # https://developer.salesforce.com/page/Digging_Deeper_into_OAuth_2.0_on_Force.com#Obtaining_a_Token_in_an_Autonomous_Client_.28Username_and_Password_Flow.29
        # curl example:
        # curl -v https://login.salesforce.com/services/oauth2/token -d "grant_type=password" -d "client_id=3MVG9d8..z.hDcPJxg3SNKy1bvkwt28Kkqa2wuBTYu_iTEmn3PgGq17zW7S3wyRUhan9cbLcFRTKrcv80XrtY" -d "client_secret=5592036841034327676" -d "username=dylan.mctaggart@butterfly.com.au" -d "password=Butterfly16fyQH3ZFE6dDO8HVbAbC8XXFM"
        loginUrl = "https://login.salesforce.com/services/oauth2/token"
        header = {"Content-Type": "application/x-www-form-urlencoded"}

        data = {
            'grant_type': 'password',
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'username': self.username,
            'password': self.password + self.api_token,
        }
        try:
            r = requests.post(loginUrl, headers=header, data=data)
            body = r.json()
        except Exception as ex:
            msg = "[Chatter] couldn't get login token  >> %s" % ex
            log.error(msg)
            raise ex

        # response example
        # {
        #     "access_token": "00D7F000000yNxR!ARsAQBRuTMMss0gd9YQ_JhaFy.oonNBdTlSUFcOLf.jwSBuTiCJPXa0kajtQYMoRhS2Ka8CiFAdpmt9mlxnJogz542v5LzUf",
        #     "instance_url": "https://ap5.salesforce.com",
        #     "id": "https://login.salesforce.com/id/00D7F000000yNxRUAU/0057F000000J99QQAS",
        #     "token_type": "Bearer",
        #     "issued_at": "1505263023689",
        #     "signature": "3Bmqk9jeKfDa26vluA2qAozvEjh4xPvkXl2djx804a0="
        # }
        return body['access_token'], body['instance_url'].rstrip('/'), body[
            'id'], body['token_type'], \
               body['issued_at'], body['signature']

    def _check_token(self):
        timestamp = int(self.issued_at)
        local_tz = pytz.timezone("Australia/Victoria")
        utc_dt = datetime.utcfromtimestamp(timestamp // 1000).replace(
            microsecond=timestamp % 1000 * 1000)
        utc_dt = utc_dt.replace(tzinfo=pytz.utc)
        local_dt = local_tz.normalize(utc_dt.astimezone(local_tz))
        token_expired_dt = local_dt + timedelta(hours=2)
        if timezone.now() >= token_expired_dt:
            self._refresh_client()

    def _refresh_client(self):
        self.access_token, self.instance_url, self.id_url, self.token_type, self.issued_at, self.signature = self.login()
        return

    def _get_file_url(self, salesforce_id):
        return '%s/services/data/v%s/connect/files/%s' % (
        self.instance_url, DEFAULT_API_VERSION, salesforce_id)

    def get_token_url_content(self, url):
        self._check_token()
        # get access token protected url from salesforce, return the content
        header = {
            'Authorization': '%s %s' % (self.token_type, self.access_token)}
        r = requests.get(url, headers=header)
        return r

    def download_url(self, url, path):
        self._check_token()
        # download a token protected link and store locally
        r = self.get_token_url_content(url)
        with open(path, 'wb') as f:
            f.write(r.content)

    def get_download_url_by_document_id(self, salesforce_id):
        self._check_token()

        # on file on saleforce have different version, this get newest version download link
        header = {
            'Authorization': '%s %s' % (self.token_type, self.access_token)}
        r = requests.get(self._get_file_url(salesforce_id), headers=header)
        body = r.json()
        if (r.status_code > 299):
            return False, body[0]['errorCode'], body[0]['message']
        else:
            return True, body['id'], self.instance_url + body['downloadUrl']

    def upload_file(self, display_name, local_file_path,
                    file_salesforce_id=None):
        with open(local_file_path, 'rb') as file_object:
            success, sf_id, download_url = self.upload_file_obj(display_name,
                                                                file_object,
                                                                file_salesforce_id)
        return success, sf_id, download_url

    def upload_file_obj(self, display_name, local_file_obj,
                        file_salesforce_id=None):
        # https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/quickreference_post_binary_file.htm
        # https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/intro_input.htm
        # curl example: curl -H "X-PrettyPrint: 1" -F 'json={"title":"BoatPrices"};type=application/json' -F "fileData=@package.json;type=application/json" -X POST https://ap5.salesforce.com/services/data/v38.0/connect/files/users/me -H 'Authorization: Bearer 00D7F000000yNxR!ARsAQBRuTMMss0gd9YQ_JhaFy.oonNBdTlSUFcOLf.jwSBuTiCJPXa0kajtQYMoRhS2Ka8CiFAdpmt9mlxnJogz542v5LzUf' --insecure
        self._check_token()

        url = '%s/services/data/v%s/connect/files/users/me' % (
        self.instance_url, DEFAULT_API_VERSION)

        if file_salesforce_id:
            # check exist on salesforce
            try:
                settings.SALESFORCE_CLIENT.ContentDocument.get(
                    file_salesforce_id)
                # existed file, update a new version
                url = self._get_file_url(file_salesforce_id)
            except SalesforceResourceNotFound:
                # uploaded before but deleted from salesforce, treat like new
                pass

        header = {
            'Authorization': '%s %s' % (self.token_type, self.access_token),
            'Accept': 'application/json'}
        mime = magic.Magic(mime=True)
        file_buffer = local_file_obj.read()
        mime_type = mime.from_buffer(file_buffer)

        payload = {"title": display_name}

        files = {'json': (None, json.dumps(payload), 'application/json'),
                 'fileData': (local_file_obj.name, file_buffer, mime_type)}

        r = requests.post(url, headers=header, files=files)
        # response example
        # {'renditionUrl240By180': '/services/data/v38.0/connect/files/0697F000000TXGJQA4/rendition?type=THUMB240BY180', 'thumb120By90RenditionStatus': 'NotScheduled', 'motif': {'mediumIconUrl': '/img/content/content32.png', 'smallIconUrl': '/img/icon/files16.png', 'color': 'BAAC93', 'svgIconUrl': None, 'largeIconUrl': '/img/content/content64.png'}, 'type': 'File', 'renditionUrl': '/services/data/v38.0/connect/files/0697F000000TXGJQA4/rendition?type=THUMB120BY90', 'moderationFlags': None, 'name': 'test.txt', 'isMajorVersion': True, 'contentModifiedDate': '2017-09-13T07:04:22.000Z', 'externalFilePermissionInformation': None, 'mimeType': 'text/plain', 'pdfRenditionStatus': 'NotScheduled', 'contentUrl': None, 'topics': {'topics': [], 'currentPageUrl': None, 'nextPageUrl': None}, 'origin': 'Chatter', 'fileType': 'Text', 'id': '0697F000000TXGJQA4', 'title': 'test.txt', 'description': None, 'fileAsset': None, 'checksum': 'e1758ae79b29d99b7e5c0da6048202a9', 'mySubscription': None, 'sharingOption': 'Allowed', 'thumb720By480RenditionStatus': 'NotScheduled', 'modifiedDate': '2017-09-13T07:04:22.000Z', 'textPreview': None, 'publishStatus': 'PrivateAccess', 'sharingRole': 'Owner', 'contentSize': 31, 'isInMyFileSync': False, 'thumb240By180RenditionStatus': 'NotScheduled', 'parentFolder': None, 'owner': {'displayName': 'Dylan McTaggart', 'mySubscription': None, 'isActive': True, 'isInThisCommunity': True, 'lastName': 'McTaggart', 'type': 'User', 'companyName': None, 'firstName': 'Dylan', 'additionalLabel': None, 'id': '0057F000000J99QQAS', 'name': 'Dylan McTaggart', 'title': None, 'motif': {'mediumIconUrl': '/img/icon/profile32.png', 'smallIconUrl': '/img/icon/profile16.png', 'color': '65CAE4', 'svgIconUrl': None, 'largeIconUrl': '/img/icon/profile64.png'}, 'url': '/services/data/v38.0/chatter/users/0057F000000J99QQAS', 'userType': 'Internal', 'communityNickname': 'dylan', 'reputation': None, 'photo': {'standardEmailPhotoUrl': 'https://ap5.salesforce.com/img/userprofile/default_profile_45_v2.png?fromEmail=1', 'photoVersionId': None, 'largePhotoUrl': 'https://c.ap5.content.force.com/profilephoto/005/F', 'mediumPhotoUrl': 'https://c.ap5.content.force.com/profilephoto/005/M', 'url': '/services/data/v38.0/connect/user-profiles/0057F000000J99QQAS/photo', 'fullEmailPhotoUrl': 'https://ap5.salesforce.com/img/userprofile/default_profile_200_v2.png?fromEmail=1', 'smallPhotoUrl': 'https://c.ap5.content.force.com/profilephoto/005/T'}}, 'flashRenditionStatus': 'NotScheduled', 'versionNumber': '1', 'renditionUrl720By480': '/services/data/v38.0/connect/files/0697F000000TXGJQA4/rendition?type=THUMB720BY480', 'downloadUrl': '/services/data/v38.0/connect/files/0697F000000TXGJQA4/content?versionNumber=1', 'pageCount': 0, 'url': '/services/data/v38.0/connect/files/0697F000000TXGJQA4?versionNumber=1', 'externalDocumentUrl': None, 'fileExtension': 'txt', 'repositoryFileId': None, 'contentHubRepository': None, 'repositoryFileUrl': None}

        body = r.json()
        if (r.status_code > 299):
            return False, body[0]['errorCode'], body[0]['message']
        else:
            return True, body['id'], self.instance_url + body['downloadUrl']


chatter = Chatter()
