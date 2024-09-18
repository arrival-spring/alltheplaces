import json
from typing import Any, Iterable
from urllib.parse import urlencode

from scrapy import Request
from scrapy.http import JsonRequest, Response
from scrapy.spiders import Spider

from locations.dict_parser import DictParser
from locations.hours import OpeningHours
from locations.items import Feature
from locations.categories import Extras, apply_yes_no, PaymentMethods
from locations.structured_data_spider import clean_facebook

GOOGLE_ATTRIBUTES_MAP = {
    "has_restroom": Extras.Toilets,
    "has_wheelchair_accessible_restroom": Extras.TOILETS_WHEELCHAIR,
    "has_takeout": Extras.TAKEAWAY,
    "has_delivery": Extras.DELIVERY,
    "accepts_reservations": Extras.RESERVATIONS, # NEEDS ADDING TO EXTRAS
    "has_high_chairs": Extras.HIGH_CHAIR, # NEEDS ADDING TO EXTRAS
    "pay_debit_card": PaymentMethods.DEBIT_CARDS,
    "pay_mobile_nfc": PaymentMethods.CONTACTLESS,
    "pay_credit_card": PaymentMethods.CREDIT_CARDS,
}

GOOGLE_WHEELCHAIR_KEYS = [
    "has_wheelchair_accessible_restroom",
    "has_wheelchair_accessible_entrance",
    "has_wheelchair_accessible_seating",
]

class YextAnswersSpider(Spider):
    dataset_attributes = {"source": "api", "api": "yext"}

    endpoint: str = "https://liveapi.yext.com/v2/accounts/me/answers/vertical/query"
    api_key: str = ""
    experience_key: str = ""
    api_version: str = "20220511"
    page_limit: int = 50
    locale: str = "en"
    environment: str = "PRODUCTION"  # "STAGING" also used
    feature_type: str = "locations"  # "restaurants" also used

    def make_request(self, offset: int) -> JsonRequest:
        return JsonRequest(
            url="{}?{}".format(
                self.endpoint,
                urlencode(
                    {
                        "experienceKey": self.experience_key,
                        "api_key": self.api_key,
                        "v": self.api_version,
                        "version": self.environment,
                        "locale": self.locale,
                        "verticalKey": self.feature_type,
                        "filters": json.dumps(
                            {"builtin.location": {"$near": {"lat": 0, "lng": 0, "radius": 50000000}}}
                        ),
                        "limit": str(self.page_limit),
                        "offset": str(offset),
                        "source": "STANDARD",
                    }
                ),
            ),
            meta={"offset": offset},
        )

    def start_requests(self) -> Iterable[Request]:
        yield self.make_request(0)

    def parse(self, response: Response, **kwargs: Any) -> Any:
        for location in response.json()["response"]["results"]:
            location = location["data"]
            item = DictParser.parse(location)
            item["branch"] = location.get("geomodifier")

            phones = []
            for phone_type in ["localPhone", "mainPhone", "mobilePhone"]:
                if phone := location["profile"].get(phone_type):
                    if isinstance(phone, dict):
                        phones.append(phone.get("number"))
                    elif isinstance(phone, str):
                        phones.append(phone)
            if len(phones) > 0:
                item["phone"] = "; ".join(phones)

            if emails := location["profile"].get("emails"):
                item["email"] = "; ".join(emails)

            item["extras"]["ref:google"] = location.get("googlePlaceId")
            item["twitter"] = location.get("twitterHandle")
            item["extras"]["contact:instagram"] = location.get("instagramHandle")
            if "facebookVanityUrl" in location:
                item["facebook"] = clean_facebook(location["facebookVanityUrl"])
            else:
                item["facebook"] = clean_facebook(location.get("facebookPageUrl"))

            if website_url_dict := location.get("websiteUrl"):
                if website_url_dict.get("preferDisplayUrl"):
                    item["website"] = website_url_dict.get("displayUrl")
                else:
                    item["website"] = website_url_dict.get("url")

            if menu_url_dict := location.get("menuUrl"):
                if menu_url_dict.get("preferDisplayUrl"):
                    item["website"] = menu_url_dict.get("displayUrl")
                else:
                    item["website"] = menu_url_dict.get("url")

            item["opening_hours"] = self.parse_opening_hours(location.get("hours"))
            item["extras"]["opening_hours:delivery"] = self.parse_opening_hours(location.get("deliveryHours"))

            if payment_methods := location.get("paymentOptions"):
                payment_methods = [p.lower().replace(" ", "") for p in payment_methods]
                apply_yes_no(PaymentMethods.AMERICAN_EXPRESS, item, "americanexpress" in payment_methods)
                apply_yes_no(PaymentMethods.APPLE_PAY, item, "applepay" in payment_methods)
                apply_yes_no(PaymentMethods.CASH, item, "cash" in payment_methods)
                apply_yes_no(PaymentMethods.CHEQUE, item, "check" in payment_methods)
                apply_yes_no(PaymentMethods.CONTACTLESS, item, "contactlesspayment" in payment_methods)
                apply_yes_no(PaymentMethods.DINERS_CLUB, item, "dinersclub" in payment_methods)
                apply_yes_no(PaymentMethods.DISCOVER_CARD, item, "discover" in payment_methods)
                apply_yes_no(PaymentMethods.MASTER_CARD, item, "mastercard" in payment_methods)
                apply_yes_no(PaymentMethods.SAMSUNG_PAY, item, "samsungpay" in payment_methods)
                apply_yes_no(PaymentMethods.VISA, item, "visa" in payment_methods)

            if google_attributes := location.get("googleAttributes"):
                for key, attribute in GOOGLE_ATTRIBUTES_MAP:
                    if key in google_attributes:
                        apply_yes_no(attribute, item, google_attributes[key][0], True)
                if "requires_reservations" in google_attributes and google_attributes["requires_reservations"][0]:
                    item["extras"]{Extras.RESERVATIONS] = "required"
                wheelchair_keys_present = [key for key in GOOGLE_WHEELCHAIR_KEYS if key in google_attributes]
                if all([google_attributes[key][0] for key in wheelchair_keys_present]):
                    apply_yes_no(Extras.WHEELCHAIR, item, True, True)
                elif any([google_attributes[key][0] for key in wheelchair_keys_present]):
                    item["extras"][Extras.WHEELCHAIR] = "limited"
                else:
                    apply_yes_no(Extras.WHEELCHAIR, item, True, True)

            yield from self.parse_item(location, item) or []

        if len(response.json()["response"]["results"]) == self.page_limit:
            yield self.make_request(response.meta["offset"] + self.page_limit)

    def parse_opening_hours(self, hours: dict, **kwargs: Any) -> OpeningHours | None:
        oh = OpeningHours()
        if not hours:
            return None
        for day, rule in hours.items():
            if not isinstance(rule, dict):
                continue
            if day == "holidayHours":
                continue
            if rule.get("isClosed") is True:
                oh.set_closed(day)
            for time in rule["openIntervals"]:
                oh.add_range(day, time["start"], time["end"])

        return oh

    def parse_item(self, location: dict, item: Feature) -> Iterable[Feature]:
        yield item
