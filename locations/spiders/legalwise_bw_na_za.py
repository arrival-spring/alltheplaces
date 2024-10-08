from locations.storefinders.location_bank import LocationBankSpider


class LegalwiseBWNAZASpider(LocationBankSpider):
    name = "legalwise_bw_na_za"
    client_id = "1f58b485-a7fe-4fc2-b78a-41dd07f9f736"
    item_attributes = {"brand": "LegalWise", "brand_wikidata": "Q61442307"}

    def post_process_item(self, item, response, location):
        item["phone"] = item["phone"].replace("010 271 2255", "")
        yield item
