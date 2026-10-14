OUT = '/tmp/claude-0/-home-user-azizbek-ai-assistant/b9f8c7f2-b197-5b44-87b0-dd972142e62d/scratchpad/mock'
def test_pb(app):
    import pymupdf
    from surxon.pdfdoc import build_blanks_pdf
    with app.test_request_context():
        a = build_blanks_pdf([{'number': 'PB-0123', 'token': 'tok123', 'id': 1}, {'number': 'PB-0124', 'token': 'tok124', 'id': 2}],
                             company='SURXON', domain='surxan-paxta.uz', brigadiers=['Nurim ota', 'Juma ota', 'Bayram og‘a'])
        app.config['SURXON'].TELEGRAM_BOT_USERNAME = 'SurxonPaxtaBot'
        a = build_blanks_pdf([{'number': 'PB-0123', 'token': 'tok123', 'id': 1}], company='SURXON', domain='surxan-paxta.uz', brigadiers=['Nurim ota', 'Juma ota', 'Bayram og‘a'])
    d = pymupdf.open(stream=a, filetype='pdf')
    d[0].get_pixmap(dpi=110).save(f'{OUT}/pb_v3_full.png')
    d[0].get_pixmap(dpi=200, clip=pymupdf.Rect(0, 0, 595, 421)).save(f'{OUT}/pb_v3.png')
