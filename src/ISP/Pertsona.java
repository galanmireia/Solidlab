package ISP;

public class Pertsona implements Yearsable{
	String name;
	Integer adina;

	public Pertsona(String name, Integer adina) {
		super();
		this.name = name;
		this.adina = adina;
	}

	@Override
	public int getYears() {
		// TODO Auto-generated method stub
		return adina;
	}
	
	double calcCovid19Impact() {

		double impact;

		//calculate increment
		Increment i=new Increment();
		int adina=getYears();
	
		
		double increment=i.getIncrementByYear(adina,40.00);
		


		//calculate impact
		Impact im= new Impact();

		impact= im.calculateImpact(adina,40.00, increment);


		return impact;
	}

	public String getName() {
		return name;
	}

	public void setName(String name) {
		this.name = name;
	}

	public Integer getAdina() {
		return adina;
	}

	public void setAdina(Integer adina) {
		this.adina = adina;
	}
}
